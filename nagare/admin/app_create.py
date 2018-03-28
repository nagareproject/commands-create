# --
# Copyright (c) 2008-2018 Net-ng.
# All rights reserved.
#
# This software is licensed under the BSD License, as described in
# the file LICENSE.txt, which you should have received as part of
# this distribution.
# --

import os
import urlparse
from copy import copy

from nagare.admin import command
from nagare.services import plugins
from cookiecutter import main, exceptions, log


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

        return sorted(templates + aliases, key=lambda (entry, plugin): self.load_order(plugin))


class Create(command.Command):
    DESC = 'Create an application structure'
    WITH_CONFIG_FILENAME = False

    def set_arguments(self, parser):
        parser.add_argument('-l', '--list', action='store_true', help='list the available templates')
        parser.add_argument('template', default='default', nargs='?', help='template to use')
        parser.add_argument('path', default='', nargs='?', help='path into the template directory')

        parser.add_argument('-c', '--config-file', help='User configuration file path')
        parser.add_argument('--no-input', action='store_true', help="don't prompt the user; use default settings")
        parser.add_argument('--checkout', help='the branch, tag or commit ID to checkout after clone')
        parser.add_argument('-v', '--verbose', action='store_true', help='print debug information')
        parser.add_argument(
            '-r', '--replay', action='store_true',
            help='Do not prompt for parameters and only use information entered previously'
        )
        parser.add_argument('-o', '--output-dir', default='', help='directory where to generate the project into')
        parser.add_argument(
            '-f', '--overwrite', action='store_true',
            help="overwrite the contents of the output directory if it already exists"
        )

        super(Create, self).set_arguments(parser)

    def list(self, template, **config):
        templates = Templates()
        default = templates.pop('default', None)

        if template and (template in templates):
            templates = {template: templates[template]}

        padding = len(max(templates, key=len))

        print 'Available templates:'
        for name in sorted(templates):
            print ' - %s:%s' % (name.ljust(padding), templates[name].DESC)

        if default is not None:
            print
            print ' * default:', default.DESC

        return 0

    def create(self, template, path, verbose, overwrite, **config):
        path = path.lstrip(os.sep)
        url = urlparse.urlsplit(template)

        if not url.scheme:
            if (os.sep not in template) and not os.path.exists(template):
                templates = Templates()
                template = templates[template].path

                if path:
                    template = os.path.join(template, path)

        log.configure_logger('DEBUG' if verbose else 'INFO')

        try:
            main.cookiecutter(template, overwrite_if_exists=overwrite, **config)
        except exceptions.RepositoryNotFound as e:
            if not url.scheme or not path:
                raise

            repo_dir = e.args[0].splitlines()[-1]
            template = os.path.basename(repo_dir)
            main.cookiecutter(os.path.join(template, path), overwrite_if_exists=overwrite, **config)

        return 0

    def run(self, list, **config):
        try:
            status = (self.list if list else self.create)(**config)
        except Exception as e:
            if e.args:
                print e.args[0]
            status = 1

        return status
