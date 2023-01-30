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
import shutil
import subprocess

try:
    import urlparse
except ImportError:
    import urllib.parse as urlparse

from cookiecutter import main, repository
from nagare.admin import admin
from nagare.config import config_from_file

NAGARE_TEMPLATE_FILE = '.nagare-template.json'
NAGARE_TEMPLATE_BRANCH = 'nagare-template'
NAGARE_TEMPLATES_REPOSITORY = 'https://github.com/nagareproject/templates.git#{0}'


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
        config = config_from_file(user_data_file)
        config = config.get('templates', config.get('cookiecutter', {})) if has_user_data_file else {}

        templates_config = main.get_user_config(default_config=True)

        default_context = templates_config['default_context']
        default_context.update(config.get('default_context', {}))

        abbreviations = templates_config.pop('abbreviations')
        abbreviations['nt'] = NAGARE_TEMPLATES_REPOSITORY
        abbreviations.update(config.get('abbreviations', {}))

        return abbreviations, templates_config['cookiecutters_dir'], default_context

    def determine_repo_dir(self, template, checkout, no_input):
        abbreviations, cookiecutters_dir, default_context = self.get_templates_config()
        repo, path = self.split_repo(repository.expand_abbreviations(template, abbreviations))

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
    def generate_context(repo_dir, default_context):
        return main.generate_context(
            context_file=os.path.join(repo_dir, 'cookiecutter.json'), default_context=default_context
        )

    @staticmethod
    def create_project(template, repo_dir, cookiecutter, upgrade, overwrite, skip, output_dir, cleanup):
        project_dir = main.generate_files(
            repo_dir=repo_dir,
            context={'cookiecutter': dict(cookiecutter, _upgrade=upgrade)},
            output_dir=output_dir,
            overwrite_if_exists=overwrite,
            skip_if_file_exists=skip,
        )

        with open(os.path.join(project_dir, NAGARE_TEMPLATE_FILE), 'w') as f:
            cookiecutter['_template'] = template
            json.dump(cookiecutter, f, indent=4, sort_keys=True)

        if cleanup:
            main.rmtree(repo_dir)


class Create(Command):
    DESC = 'create an application structure from a template'

    def set_arguments(self, parser):
        parser.add_argument('-l', '--list', action='store_true', help='list available abbreviations')
        parser.add_argument('template', help='template to apply')

        parser.add_argument('--no-input', action='store_true', help="don't prompt the user; use default settings")
        parser.add_argument('-o', '--output-dir', default='.', help='directory to generate the project into')
        parser.add_argument(
            '-f',
            '--overwrite',
            action='store_true',
            help='overwrite the contents of the output directory if it already exists',
        )
        parser.add_argument('-s', '--skip', default=False, help='skip already existing files')

        super(Create, self).set_arguments(parser)

    def list(self, **config):
        abbreviations, _, _ = self.get_templates_config()
        padding = len(max(abbreviations, key=len))

        print('Available abbreviations:')
        for abbr, url in sorted(abbreviations.items()):
            print(' - {}: {}'.format(abbr.ljust(padding), url))

        return 0

    def create(self, template, version, no_input, output_dir, overwrite, skip):
        repo_uri, repo_dir, cleanup, default_context = self.determine_repo_dir(template, version, no_input)

        print("Generating project from '{}'\n".format(repo_uri))

        context = self.generate_context(repo_dir, default_context)
        cookiecutter = main.prompt_for_config(context, no_input)

        self.create_project(template, repo_dir, cookiecutter, False, overwrite, skip, output_dir, cleanup)

        return 0

    def run(self, list, **config):
        if not self.logger.handlers:
            self.logger.addHandler(logging.StreamHandler())

        try:
            status = (self.list if list else self.create)(**config)
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
            firstref = subprocess.check_output(
                ['git', 'rev-list', '--max-parents=0', '--max-count=1', 'HEAD'],
                universal_newlines=True,
                stderr=subprocess.DEVNULL,
                cwd=directory,
            ).strip()

            self.git(['branch', NAGARE_TEMPLATE_BRANCH, firstref], directory)

        return True

    def upgrade(self, template, version, merge, ignore, directory):
        if not self.logger.handlers:
            self.logger.addHandler(logging.StreamHandler())

        directory = os.path.abspath(directory)
        if not self.create_template_branch(directory):
            return 1

        work_directory = os.path.join(directory, '.git', 'nagare-template')
        git_directory = os.path.join(work_directory, os.path.basename(directory))
        self.git(['worktree', 'add', '--no-checkout', git_directory, NAGARE_TEMPLATE_BRANCH], directory)

        try:
            # Generate appli from the template
            # --------------------------------

            # Read previous template parameters
            with open(os.path.join(directory, NAGARE_TEMPLATE_FILE)) as f:
                context = json.load(f)

            template = template or context['_template']
            repo_uri, repo_dir, cleanup, _ = self.determine_repo_dir(template, version, True)

            print("Upgrading project from '{}'\n".format(repo_uri))

            # Generate a project from the new template version, with the previous project parameters
            self.create_project(template, repo_dir, context, True, True, False, work_directory, cleanup)

            # Commit changes to main branch
            # -----------------------------

            self.git(['add', '-A', '.'], git_directory)

            if ignore:
                self.git(['reset', 'HEAD'] + ignore, git_directory)
                self.git(['checkout'] + ignore, git_directory)

            if not self.git(['diff-index', '--quiet', 'HEAD', '--'], git_directory, False):
                print('No changes found')
            else:
                self.git(['commit', '-nm', 'Update template'], git_directory)
                if not merge:
                    print(
                        "Changes in branch '{}' not apply to 'master'. Manual merge needed".format(
                            NAGARE_TEMPLATE_BRANCH
                        )
                    )
                else:
                    self.git(['merge', NAGARE_TEMPLATE_BRANCH], directory, stderr=None, stdout=None)
        finally:
            shutil.rmtree(work_directory)
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
