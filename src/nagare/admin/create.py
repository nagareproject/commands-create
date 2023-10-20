# --
# Copyright (c) 2008-2023 Net-ng.
# All rights reserved.
#
# This software is licensed under the BSD License, as described in
# the file LICENSE.txt, which you should have received as part of
# this distribution.
# --

import json
import logging
import os
import pathlib
import shutil
import subprocess

try:
    import urlparse
except ImportError:
    import urllib.parse as urlparse

from cookiecutter import main, repository
from jinja2 import environment, ext
from nagare.admin import admin
from nagare.config import config_from_file
from slugify.slugify import slugify

NAGARE_TEMPLATE_FILE = '.nagare-template.json'
NAGARE_TEMPLATE_BRANCH = 'nagare-template'
NAGARE_TEMPLATES_REPOSITORY = 'https://github.com/nagareproject/templates.git#{0}'


def repository_has_template_file(repo_directory, template_file):
    repo_directory_exists = os.path.isdir(repo_directory)
    repo_config_exists = os.path.isfile(os.path.join(repo_directory, template_file))

    return repo_directory_exists and repo_config_exists


def is_repository(repo_directory):
    return repository_has_template_file(repo_directory, 'template.json') or repository_has_template_file(
        repo_directory, 'cookiecutter.json'
    )


repository.repository_has_cookiecutter_json = is_repository


class JinjaTemplate(environment.Template):
    def render(self, cookiecutter):
        return super().render(cookiecutter=cookiecutter, context=cookiecutter)


class JinjaExtension(ext.Extension):
    def __init__(self, env):
        super().__init__(env)

        env.template_class = JinjaTemplate
        env.filters['snakecase'] = lambda v: slugify(v, separator='_')
        env.filters['camelcase'] = lambda v: slugify(v).title().replace('-', '')


class Command(admin.Command):
    WITH_CONFIG_FILENAME = False

    def set_arguments(self, parser):
        parser.add_argument('--version', help='template branch, tag or commit to apply')

        super(Command, self).set_arguments(parser)

    def _create_services(cls, config, config_filename, roots=(), global_config=None):
        return cls.SERVICES_FACTORY()

    @staticmethod
    def split_repo(repo):
        url = list(urlparse.urlsplit(repo))
        if url[0]:
            url[4], path = '', url[4]
            path = path.strip('/')
            repo = urlparse.urlunsplit(url)
        else:
            repo, path = '', repo

        return repo, path

    def get_templates_config(self):
        has_user_data_file, user_data_file = self.get_user_data_file()
        config = config_from_file(user_data_file) if has_user_data_file else {}
        config = config.get('templates', config.get('cookiecutter', {})) if has_user_data_file else {}

        templates_config = main.get_user_config(default_config=True)

        default_context = templates_config['default_context']
        default_context.update(config.get('default_context', {}))

        abbreviations = templates_config.pop('abbreviations')
        abbreviations['nt'] = NAGARE_TEMPLATES_REPOSITORY
        abbreviations.update(config.get('abbreviations', {}))

        return abbreviations, templates_config['cookiecutters_dir'], default_context

    def expand_abbreviations(self, template):
        abbreviations, _, _ = self.get_templates_config()
        return repository.expand_abbreviations(template, abbreviations)

    def determine_repo_dir(self, template, checkout, no_input):
        _, cookiecutters_dir, default_context = self.get_templates_config()
        repo, path = self.split_repo(self.expand_abbreviations(template))

        repo_dir, cleanup = main.determine_repo_dir(
            template=repo,
            abbreviations={},
            clone_to_dir=cookiecutters_dir,
            checkout=checkout,
            no_input=no_input,
            directory=path,
        )

        return (repo + '#' + path).strip('#'), repo_dir, cleanup, default_context

    @staticmethod
    def retreive_inherited_context(output_dir):
        context = {}
        for parent in reversed(pathlib.Path(output_dir).absolute().parents):
            template_config_filename = parent / NAGARE_TEMPLATE_FILE
            if template_config_filename.is_file():
                with template_config_filename.open() as f:
                    context.update(json.load(f))

        return context

    @classmethod
    def generate_context(cls, repo_dir, default_context, output_dir):
        default_context.update(cls.retreive_inherited_context(output_dir))

        context_file = os.path.join(repo_dir, 'template.json')
        if not os.path.isfile(context_file):
            context_file = os.path.join(repo_dir, 'cookiecutter.json')

        context = main.generate_context(context_file=context_file, default_context=default_context)
        context.setdefault('cookiecutter', context.pop('template', None))
        context['cookiecutter']['_extensions'] = ['nagare.admin.create.JinjaExtension']

        return default_context, context

    @staticmethod
    def create_project(template, repo_dir, inherited_context, context, upgrade, overwrite, skip, output_dir, cleanup):
        context = dict(
            context, _upgrade=upgrade, _output_dir=os.path.abspath(output_dir), _cur_dir=os.path.abspath(os.curdir)
        )
        project_dir = main.generate_files(
            repo_dir=repo_dir,
            context={'cookiecutter': context},
            output_dir=output_dir,
            overwrite_if_exists=overwrite,
            skip_if_file_exists=skip,
        )

        with open(os.path.join(project_dir, NAGARE_TEMPLATE_FILE), 'w') as f:
            context = {k: context[k] for k in set(context) - set(inherited_context) if not k.startswith('_')}
            context['_template'] = template
            json.dump(context, f, indent=4, sort_keys=True)

        if cleanup:
            main.rmtree(repo_dir)


class Create(Command):
    DESC = 'create an application structure from a template'

    @staticmethod
    def parameter(parameter):
        parameter, value = parameter.split('=', 1)
        return parameter, value

    def set_arguments(self, parser):
        parser.add_argument('template', nargs='?', help='template to apply')
        parser.add_argument(
            'parameter',
            nargs='*',
            metavar='parameter=value',
            type=None if ' ' in parser.prog else self.parameter,
            help='template parameter',
        )

        parser.add_argument('--no-input', action='store_true', help="don't prompt the user; use default settings")
        parser.add_argument('-o', '--output-dir', default='.', help='directory to generate the project into')
        parser.add_argument(
            '-f',
            '--force',
            action='store_true',
            help='overwrite the contents of the output directory if it already exists',
        )
        parser.add_argument('-s', '--skip', action='store_true', help='skip already existing files')

        super(Create, self).set_arguments(parser)

    def _run(self, command_names, template, version, no_input, output_dir, force, skip, parameter, **kw):
        if template and len(command_names) != 1:
            args = (
                (['--version', version] if version else [])
                + (['--no-input'] if no_input else [])
                + ['--output-dir', output_dir]
                + (['--force'] if force else [])
                + (['--skip'] if skip else [])
                + self.expand_abbreviations(template).split()
                + parameter
            )

            self.execute(args=args)
        else:
            return super(Create, self)._run(
                command_names,
                template=template,
                version=version,
                no_input=no_input,
                output_dir=output_dir,
                overwrite=force,
                skip=skip,
                parameters=parameter,
                **kw,
            )

    def list(self, template, **config):
        abbreviations, _, _ = self.get_templates_config()
        padding = len(max(abbreviations, key=len))

        print('Available abbreviations:')
        for abbr, url in sorted(abbreviations.items()):
            print(' - {}: {}'.format(abbr.ljust(padding), url))

        return 0

    def create(self, template, version, no_input, output_dir, overwrite, skip, parameters):
        repo_uri, repo_dir, cleanup, default_context = self.determine_repo_dir(template, version, no_input)

        print("Generating project from '{}'\n".format(repo_uri))

        inherited_context, context = self.generate_context(repo_dir, default_context, output_dir)
        context['cookiecutter'].update(dict(parameters))

        context = main.prompt_for_config(context, no_input)
        self.create_project(template, repo_dir, inherited_context, context, False, overwrite, skip, output_dir, cleanup)

        return 0

    def run(self, template, **config):
        if not self.logger.handlers:
            self.logger.addHandler(logging.StreamHandler())

        try:
            status = (self.list if not template else self.create)(template, **config)
        except subprocess.CalledProcessError as e:
            if e.args:
                self.logger.error('Error [{}] for command: {}'.format(e.args[0], ' '.join(e.args[1])))
                status = e.args[0]
            else:
                self.logger.error('Git error')
                status = 1
        except Exception as e:
            if e.args:
                self.logger.error(e.args[0])
            status = 1

        return status


class Upgrade(Command):
    DESC = 'upgrade an application structure from a template'

    def set_arguments(self, parser):
        parser.add_argument('-t', '--template', default=None, help='template to apply')
        parser.add_argument(
            '-n', '--no-merge', action='store_false', dest='merge', help="Don't merge changes to master"
        )
        parser.add_argument(
            '-i',
            '--ignore',
            action='append',
            help='pattern of files to ignore for changes (can be given multiple times)',
        )

        parser.add_argument('directory', help='root project directory')

        super(Upgrade, self).set_arguments(parser)

    @staticmethod
    def git(args, directory, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL):
        return (subprocess.check_call if check else subprocess.call)(
            ['git'] + args, stdout=stdout, stderr=stderr, cwd=directory
        )

    @staticmethod
    def git_with_result(args, directory):
        return subprocess.check_output(
            ['git'] + args, universal_newlines=True, stderr=subprocess.DEVNULL, cwd=directory
        ).strip()

    def create_template_branch(self, directory):
        # Check if a local 'nagare-template' branch already exists
        r = self.git(['rev-parse', '-q', '--verify', 'HEAD'], directory, False)
        if r:
            print("No Git repository found in directory '{}'".format(directory))
            return False

        if not self.git(['rev-parse', '-q', '--verify', NAGARE_TEMPLATE_BRANCH], directory, False):
            return True

        # No local branch found. Check if a remote 'nagare-template' branch exists
        if self.git(['rev-parse', '-q', '--verify', 'origin/' + NAGARE_TEMPLATE_BRANCH], directory, False) == 0:
            # Checkout the remote 'nagare-template' branch
            self.git(['branch', NAGARE_TEMPLATE_BRANCH, 'origin/' + NAGARE_TEMPLATE_BRANCH], directory)
        else:
            # No remote 'nagare-template' branch found. Create it from the first commit
            firstref = self.git_with_result(['rev-list', '--max-parents=0', '--max-count=1', 'HEAD'], directory)
            self.git(['branch', NAGARE_TEMPLATE_BRANCH, firstref], directory)

        return True

    def upgrade(self, template, version, merge, ignore, directory):
        if not self.logger.handlers:
            self.logger.addHandler(logging.StreamHandler())

        directory = os.path.abspath(directory)

        if not template and not os.path.isfile(os.path.join(directory, NAGARE_TEMPLATE_FILE)):
            print('Directory {} not generated from a template'.format(directory))
            return 1

        if not self.create_template_branch(directory):
            return 1

        git_directory = self.git_with_result(['rev-parse', '--show-toplevel'], directory)
        app_name = os.path.basename(git_directory)
        git_directory = os.path.join(git_directory, '.git', 'nagare-template')
        relative_directory = self.git_with_result(['rev-parse', '--show-prefix'], directory).strip(os.path.sep)
        work_directory = os.path.join(git_directory, app_name)

        self.git(
            ['worktree', 'add']
            + ([] if relative_directory else ['--no-checkout'])
            + [work_directory, NAGARE_TEMPLATE_BRANCH],
            directory,
        )

        try:
            # Generate appli from the template
            # --------------------------------

            # Read previous template parameters
            inherited_context = self.retreive_inherited_context(directory)

            with open(os.path.join(directory, NAGARE_TEMPLATE_FILE)) as f:
                context = inherited_context.copy()
                context.update(json.load(f))

            template = template or context['_template']
            repo_uri, repo_dir, cleanup, _ = self.determine_repo_dir(template, version, True)

            if relative_directory:
                dest_directory = os.path.join(work_directory, relative_directory)
                shutil.rmtree(dest_directory, True)
                dest_directory = os.path.dirname(dest_directory)
            else:
                dest_directory = git_directory

            # Generate a project from the new template version, with the previous project parameters
            self.create_project(
                template, repo_dir, inherited_context, context, True, True, False, dest_directory, cleanup
            )

            # Commit changes to main branch
            # -----------------------------

            self.git(['add', '-A', '.'], work_directory)

            if ignore:
                self.git(['reset', 'HEAD'] + ignore, work_directory)
                self.git(['checkout'] + ignore, work_directory)

            if not self.git(['diff-index', '--quiet', 'HEAD', '--'], work_directory, False):
                print('No changes found')
            else:
                self.git(['commit', '-nm', 'Updated from template'], work_directory)
                if not merge:
                    print(
                        "Changes in branch '{}' not applied to 'master'. Manual merge needed".format(
                            NAGARE_TEMPLATE_BRANCH
                        )
                    )
                else:
                    self.git(
                        ['merge', '-q', '-nm', 'Updated from template', NAGARE_TEMPLATE_BRANCH],
                        directory,
                        stderr=None,
                        stdout=None,
                    )
        finally:
            shutil.rmtree(git_directory)
            self.git(['worktree', 'prune'], directory, False)

        return 0

    def run(self, template, version, merge, ignore, directory):
        try:
            status = self.upgrade(template, version, merge, ignore, directory)
        except subprocess.CalledProcessError as e:
            if e.args:
                self.logger.error('Error [{}] for command: {}'.format(e.args[0], ' '.join(e.args[1])))
                status = e.args[0]
            else:
                self.logger.error('Git error')
                status = 1
        except Exception as e:
            if e.args:
                self.logger.error(e.args[0])
            status = 1

        return status
