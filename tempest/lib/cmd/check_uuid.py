#!/usr/bin/env python

# Copyright 2014 Mirantis, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import argparse
import ast
import contextlib
import importlib
import inspect
import os
import sys
import unittest
import urllib.parse as urlparse
import uuid

from oslo_utils import uuidutils

DECORATOR_MODULE = 'decorators'
DECORATOR_NAME = 'idempotent_id'
DECORATOR_IMPORT = 'tempest.lib.%s' % DECORATOR_MODULE
IMPORT_LINE = 'from tempest.lib import %s' % DECORATOR_MODULE
DECORATOR_TEMPLATE = "@%s.%s('%%s')" % (DECORATOR_MODULE,
                                        DECORATOR_NAME)
UNIT_TESTS_EXCLUDE = 'tempest.tests'


class SourcePatcher(object):

    """Lazy patcher for python source files"""

    def __init__(self):
        self.source_files = None
        self.patches = None
        self.clear()

    def clear(self):
        """Clear inner state"""
        self.source_files = {}
        self.patches = {}

    @staticmethod
    def _quote(s):
        return urlparse.quote(s)

    @staticmethod
    def _unquote(s):
        return urlparse.unquote(s)

    def add_patch(self, filename, patch, line_no):
        """Add lazy patch"""
        if filename not in self.source_files:
            with open(filename) as f:
                self.source_files[filename] = self._quote(f.read())
        patch_id = uuidutils.generate_uuid()
        if not patch.endswith('\n'):
            patch += '\n'
        self.patches[patch_id] = self._quote(patch)
        lines = self.source_files[filename].split(self._quote('\n'))
        lines[line_no - 1] = ''.join(('{%s:s}' % patch_id, lines[line_no - 1]))
        self.source_files[filename] = self._quote('\n').join(lines)

    @staticmethod
    def _save_changes(filename, source):
        print('%s fixed' % filename)
        with open(filename, 'w') as f:
            f.write(source)

    def apply_patches(self):
        """Apply all patches"""
        for filename in self.source_files:
            patched_source = self._unquote(
                self.source_files[filename].format(**self.patches)
            )
            self._save_changes(filename, patched_source)
        self.clear()


class TestChecker(object):

    def __init__(self, package):
        self.package = package
        self.base_path = os.path.abspath(os.path.dirname(package.__file__))

    def _path_to_package(self, path):
        relative_path = path[len(self.base_path) + 1:]
        if relative_path:
            return '.'.join((self.package.__name__,) +
                            tuple(relative_path.split('/')))
        else:
            return self.package.__name__

    def _modules_search(self):
        """Recursive search for python modules in base package"""
        modules = []
        for root, _, files in os.walk(self.base_path):
            if not os.path.exists(os.path.join(root, '__init__.py')):
                continue
            root_package = self._path_to_package(root)
            for item in files:
                if item.endswith('.py'):
                    module_name = '.'.join((root_package,
                                            os.path.splitext(item)[0]))
                    if not module_name.startswith(UNIT_TESTS_EXCLUDE):
                        modules.append(module_name)
        return modules

    @staticmethod
    def _get_idempotent_id(test_node):
        "Return key-value dict with metadata from @decorators.idempotent_id"
        idempotent_id = None
        for decorator in test_node.decorator_list:
            if (hasattr(decorator, 'func') and
                    hasattr(decorator.func, 'attr') and
                    decorator.func.attr == DECORATOR_NAME and
                    hasattr(decorator.func, 'value') and
                    decorator.func.value.id == DECORATOR_MODULE):
                for arg in decorator.args:
                    idempotent_id = ast.literal_eval(arg)
        return idempotent_id

    @staticmethod
    def _is_decorator(line):
        return line.strip().startswith('@')

    @staticmethod
    def _is_def(line):
        return line.strip().startswith('def ')

    def _add_uuid_to_test(self, patcher, test_node, source_path):
        with open(source_path) as src:
            src_lines = src.read().split('\n')
        lineno = test_node.lineno
        insert_position = lineno
        while True:
            if (self._is_def(src_lines[lineno - 1]) or
                    (self._is_decorator(src_lines[lineno - 1]) and
                        (DECORATOR_TEMPLATE.split('(')[0] <=
                            src_lines[lineno - 1].strip().split('(')[0]))):
                insert_position = lineno
                break
            lineno += 1
        patcher.add_patch(
            source_path,
            ' ' * test_node.col_offset + DECORATOR_TEMPLATE % uuid.uuid4(),
            insert_position
        )

    @staticmethod
    def _is_test_case(module, node):
        if (node.__class__ is ast.ClassDef and
                hasattr(module, node.name) and
                inspect.isclass(getattr(module, node.name))):
            return issubclass(getattr(module, node.name), unittest.TestCase)

    @staticmethod
    def _is_test_method(node):
        return (node.__class__ is ast.FunctionDef and
                node.name.startswith('test_'))

    @staticmethod
    def _next_node(body, node):
        if body.index(node) < len(body):
            return body[body.index(node) + 1]

    @staticmethod
    def _import_name(node):
        if isinstance(node, ast.Import):
            return node.names[0].name
        elif isinstance(node, ast.ImportFrom):
            return '%s.%s' % (node.module, node.names[0].name)

    @contextlib.contextmanager
    def ignore_site_packages_paths(self):
        """Removes site-packages directories from the sys.path

        Source:
            - StackOverflow: https://stackoverflow.com/questions/22195382/
            - Author: https://stackoverflow.com/users/485844/
        """

        paths = sys.path
        # remove all third-party paths
        # so that only stdlib imports will succeed
        sys.path = list(filter(
            None,
            filter(lambda i: 'site-packages' not in i, sys.path)
        ))
        yield
        sys.path = paths

    def is_std_lib(self, module):
        """Checks whether the module is part of the stdlib or not

        Source:
            - StackOverflow: https://stackoverflow.com/questions/22195382/
            - Author: https://stackoverflow.com/users/485844/
        """

        if module in sys.builtin_module_names:
            return True

        with self.ignore_site_packages_paths():
            imported_module = sys.modules.pop(module, None)
            try:
                importlib.import_module(module)
            except ImportError:
                return False
            else:
                return True
            finally:
                if imported_module:
                    sys.modules[module] = imported_module

    def _add_import_for_test_uuid(self, patcher, src_parsed, source_path):
        import_list = [node for node in src_parsed.body
                       if isinstance(node, (ast.Import, ast.ImportFrom))]

        if not import_list:
            print("(WARNING) %s: The file is not valid as it does not contain "
                  "any import line! Therefore the import needed by "
                  "@decorators.idempotent_id is not added!" % source_path)
            return

        tempest_imports = [node for node in import_list
                           if self._import_name(node) and
                           'tempest.' in self._import_name(node)]

        for node in tempest_imports:
            if self._import_name(node) < DECORATOR_IMPORT:
                continue
            else:
                line_no = node.lineno
                break
        else:
            if tempest_imports:
                line_no = tempest_imports[-1].lineno + 1

        # Insert import line between existing tempest imports
        if tempest_imports:
            patcher.add_patch(source_path, IMPORT_LINE, line_no)
            return

        # Group space separated imports together
        grouped_imports = {}
        first_import_line = import_list[0].lineno
        for idx, import_line in enumerate(import_list, first_import_line):
            group_no = import_line.lineno - idx
            group = grouped_imports.get(group_no, [])
            group.append(import_line)
            grouped_imports[group_no] = group

        if len(grouped_imports) > 3:
            print("(WARNING) %s: The file contains more than three import "
                  "groups! This is not valid according to the PEP8 "
                  "style guide. " % source_path)

        # Divide grouped_imports into groups based on PEP8 style guide
        pep8_groups = {}
        package_name = self.package.__name__.split(".")[0]
        for key in grouped_imports:
            module = self._import_name(grouped_imports[key][0]).split(".")[0]
            if module.startswith(package_name):
                group = pep8_groups.get('3rd_group', [])
                pep8_groups['3rd_group'] = group + grouped_imports[key]
            elif self.is_std_lib(module):
                group = pep8_groups.get('1st_group', [])
                pep8_groups['1st_group'] = group + grouped_imports[key]
            else:
                group = pep8_groups.get('2nd_group', [])
                pep8_groups['2nd_group'] = group + grouped_imports[key]

        for node in pep8_groups.get('2nd_group', []):
            if self._import_name(node) < DECORATOR_IMPORT:
                continue
            else:
                line_no = node.lineno
                import_snippet = IMPORT_LINE
                break
        else:
            if pep8_groups.get('2nd_group', []):
                line_no = pep8_groups['2nd_group'][-1].lineno + 1
                import_snippet = IMPORT_LINE
            elif pep8_groups.get('1st_group', []):
                line_no = pep8_groups['1st_group'][-1].lineno + 1
                import_snippet = '\n' + IMPORT_LINE
            else:
                line_no = pep8_groups['3rd_group'][0].lineno
                import_snippet = IMPORT_LINE + '\n\n'

        patcher.add_patch(source_path, import_snippet, line_no)

    def get_tests(self):
        """Get test methods with sources from base package with metadata"""
        tests = {}
        for module_name in self._modules_search():
            tests[module_name] = {}
            module = importlib.import_module(module_name)
            source_path = '.'.join(
                (os.path.splitext(module.__file__)[0], 'py')
            )
            with open(source_path, 'r') as f:
                source = f.read()
            tests[module_name]['source_path'] = source_path
            tests[module_name]['tests'] = {}
            source_parsed = ast.parse(source)
            tests[module_name]['ast'] = source_parsed
            tests[module_name]['import_valid'] = (
                hasattr(module, DECORATOR_MODULE) and
                inspect.ismodule(getattr(module, DECORATOR_MODULE))
            )
            test_cases = (node for node in source_parsed.body
                          if self._is_test_case(module, node))
            for node in test_cases:
                for subnode in filter(self._is_test_method, node.body):
                    test_name = '%s.%s' % (node.name, subnode.name)
                    tests[module_name]['tests'][test_name] = subnode
        return tests

    @staticmethod
    def _filter_tests(function, tests):
        """Filter tests with condition 'function(test_node) == True'"""
        result = {}
        for module_name in tests:
            for test_name in tests[module_name]['tests']:
                if function(module_name, test_name, tests):
                    if module_name not in result:
                        result[module_name] = {
                            'ast': tests[module_name]['ast'],
                            'source_path': tests[module_name]['source_path'],
                            'import_valid': tests[module_name]['import_valid'],
                            'tests': {}
                        }
                    result[module_name]['tests'][test_name] = \
                        tests[module_name]['tests'][test_name]
        return result

    def find_untagged(self, tests):
        """Filter all tests without uuid in metadata"""
        def check_uuid_in_meta(module_name, test_name, tests):
            idempotent_id = self._get_idempotent_id(
                tests[module_name]['tests'][test_name])
            return not idempotent_id
        return self._filter_tests(check_uuid_in_meta, tests)

    def report_collisions(self, tests):
        """Reports collisions if there are any

        Returns true if collisions exist.
        """
        uuids = {}

        def report(module_name, test_name, tests):
            test_uuid = self._get_idempotent_id(
                tests[module_name]['tests'][test_name])
            if not test_uuid:
                return
            if test_uuid in uuids:
                error_str = "%s:%s\n uuid %s collision: %s<->%s\n%s:%s" % (
                    tests[module_name]['source_path'],
                    tests[module_name]['tests'][test_name].lineno,
                    test_uuid,
                    test_name,
                    uuids[test_uuid]['test_name'],
                    uuids[test_uuid]['source_path'],
                    uuids[test_uuid]['test_node'].lineno,
                )
                print(error_str)
                print("cannot automatically resolve the collision, please "
                      "manually remove the duplicate value on the new test.")
                return True
            else:
                uuids[test_uuid] = {
                    'module': module_name,
                    'test_name': test_name,
                    'test_node': tests[module_name]['tests'][test_name],
                    'source_path': tests[module_name]['source_path']
                }
        return bool(self._filter_tests(report, tests))

    def report_untagged(self, tests):
        """Reports untagged tests if there are any

        Returns true if untagged tests exist.
        """
        def report(module_name, test_name, tests):
            error_str = ("%s:%s\nmissing @decorators.idempotent_id"
                         "('...')\n%s\n") % (
                tests[module_name]['source_path'],
                tests[module_name]['tests'][test_name].lineno,
                test_name
            )
            print(error_str)
            return True
        return bool(self._filter_tests(report, tests))

    def fix_tests(self, tests):
        """Add uuids to all specified in tests and fix it in source files"""
        patcher = SourcePatcher()
        for module_name in tests:
            add_import_once = True
            for test_name in tests[module_name]['tests']:
                if not tests[module_name]['import_valid'] and add_import_once:
                    self._add_import_for_test_uuid(
                        patcher,
                        tests[module_name]['ast'],
                        tests[module_name]['source_path']
                    )
                    add_import_once = False
                self._add_uuid_to_test(
                    patcher, tests[module_name]['tests'][test_name],
                    tests[module_name]['source_path'])
        patcher.apply_patches()


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', action='store', dest='package',
                        default='tempest', type=str,
                        help='Package with tests')
    parser.add_argument('--fix', action='store_true', dest='fix_tests',
                        help='Attempt to fix tests without UUIDs')
    parser.add_argument('--libpath', action='store', dest='libpath',
                        default=".", type=str,
                        help='Path to package')

    args = parser.parse_args()
    sys.path.append(os.path.join(os.path.dirname(__file__), os.pardir))
    sys.path.insert(0, args.libpath)
    pkg = importlib.import_module(args.package)

    checker = TestChecker(pkg)
    errors = False
    tests = checker.get_tests()
    untagged = checker.find_untagged(tests)
    errors = checker.report_collisions(tests) or errors

    if args.fix_tests and untagged:
        checker.fix_tests(untagged)
    else:
        errors = checker.report_untagged(untagged) or errors
    if errors:
        sys.exit("@decorators.idempotent_id existence and uniqueness checks "
                 "failed\n"
                 "Run 'tox -v -e uuidgen' to automatically fix tests with\n"
                 "missing @decorators.idempotent_id decorators.")


if __name__ == '__main__':
    run()
