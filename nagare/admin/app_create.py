# --
# Copyright (c) 2008-2021 Net-ng.
# All rights reserved.
#
# This software is licensed under the BSD License, as described in
# the file LICENSE.txt, which you should have received as part of
# this distribution.
# --

import os
import logging
import tempfile
import subprocess
from copy import copy

try:
    import urlparse
except ImportError:
    import urllib.parse as urlparse

import yaml
import configobj
from nagare.admin import admin
from nagare.services import plugins
from cookiecutter import main, repository, exceptions, log

NAGARE_TEMPLATES_REPOSITORY = 'https://github.com/nagareproject/templates.git#{0}'


class Commands(admin.Commands):
    DESC = 'applications management subcommands'


class Templates(plugins.Plugins):
    ENTRY_POINTS = 'nagare.templates'

    def __init__(self):
        super(Templates, self).__init__({})

    def load_activated_plugins(self, activations=None):
        templates = super(Templates, self).load_activated_plugins(activations)

        aliases = []
        for entry, template in templates:
            for name in template.names:
                entry = copy(entry)
                entry.name = name
                aliases.append((entry, template))

        return sorted(templates + aliases, key=lambda template: self.load_order(template[1]))


class Create(admin.Command):
    DESC = 'create an application structure'
    WITH_CONFIG_FILENAME = False

    def set_arguments(self, parser):
        parser.add_argument('-l', '--list', action='store_true', help='list the available templates and abbreviations')
        parser.add_argument('template', default='default', nargs='?', help='template to use')

        parser.add_argument('--no-input', action='store_true', help="don't prompt the user; use default settings")
        parser.add_argument('--checkout', help='the branch, tag or commit ID to checkout after clone')
        parser.add_argument('-v', '--verbose', action='store_true', help='print debug information')
        parser.add_argument(
            '-r', '--replay', action='store_true',
            help='do not prompt for parameters and only use information entered previously'
        )
        parser.add_argument('-o', '--output-dir', default='', help='directory to generate the project into')
        parser.add_argument(
            '-f', '--overwrite', action='store_true',
            help="overwrite the contents of the output directory if it already exists"
        )

        super(Create, self).set_arguments(parser)

    def read_user_config(self):
        def remove_empty(d):
            return {k: remove_empty(v) for k, v in d.items() if remove_empty(v)} if isinstance(d, dict) else d

        has_user_data_file, user_data_file = self.get_user_data_file()

        config = configobj.ConfigObj(user_data_file).dict().get('cookiecutter', {}) if has_user_data_file else {}
        config = remove_empty(config)
        config['abbreviations'] = dict(
            {'nt': NAGARE_TEMPLATES_REPOSITORY},
            **config.get('abbreviations', {})
        )

        return config

    def list(self, template, **config):
        print('Available abbreviations:')

        user_config = self.read_user_config()
        default_config = main.get_user_config(default_config=True)
        abbreviations = dict(default_config['abbreviations'], **user_config['abbreviations'])
        padding = len(max(abbreviations, key=len))
        for abbr, url in sorted(abbreviations.items()):
            print(' - {}: {}'.format(abbr.ljust(padding), url))

        print('')

        print('Available templates:')

        templates = Templates()
        if not templates:
            print('  <No registered templates>')
        else:
            default = templates.get('default')
            if default is not None:
                del templates['default']

            if template and (template in templates):
                templates = {template: templates[template]}

            padding = len(max(templates, key=len))

            for name in sorted(templates):
                print(' - {}: {}'.format(name.ljust(padding), templates[name].DESC))

            if default is not None:
                print('')
                print(' * default: ' + default.DESC)

        return 0

    def create(self, template, verbose, overwrite, **config):
        if verbose:
            log.configure_logger('DEBUG')

        if not self.logger.handlers:
            self.logger.addHandler(logging.StreamHandler())
        self.logger.setLevel(logging.DEBUG)

        with tempfile.NamedTemporaryFile() as cc_yaml_config:
            cc_config = self.read_user_config()
            cc_yaml_config.write(yaml.dump(
                cc_config,
                default_style='"',
                default_flow_style=False
            ).encode('utf-8'))
            cc_yaml_config.flush()

            cc_yaml_config_name = cc_yaml_config.name if cc_config else None
            cc_config = main.get_user_config(config_file=cc_yaml_config_name)

            template = repository.expand_abbreviations(template, cc_config['abbreviations'])

            url = list(urlparse.urlsplit(template))
            if url[0]:
                url[4], path = '', url[4]
                path = path.strip('/')
                template = urlparse.urlunsplit(url)
            else:
                path = None
                if (os.sep not in template) and not os.path.exists(template):
                    templates = Templates()
                    if template in templates:
                        template = templates[template].location

            print('Generating project from `{}`\n'.format(template))

            try:
                main.cookiecutter(
                    template,
                    overwrite_if_exists=overwrite, config_file=cc_yaml_config_name,
                    **config
                )
            except exceptions.RepositoryNotFound as e:
                if not path:
                    raise

                repo_dir = e.args[0].splitlines()[-1]
                template = os.path.basename(repo_dir)
                main.cookiecutter(
                    os.path.join(template, path),
                    overwrite_if_exists=overwrite, config_file=cc_yaml_config_name,
                    **config
                )

            return 0

    def run(self, list, **config):
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
