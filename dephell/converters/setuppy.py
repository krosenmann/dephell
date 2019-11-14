# built-in
from collections import defaultdict
from distutils.core import run_setup
from json import dumps as json_dumps
from logging import getLogger
from pathlib import Path
from re import sub
from typing import Optional

# external
from dephell_discover import Root as PackageRoot
from dephell_links import DirLink, FileLink, URLLink, VCSLink, parse_link
from dephell_specifier import RangeSpecifier
from packaging.requirements import Requirement
from setuptools.dist import Distribution

# app
from ..context_tools import chdir
from ..controllers import DependencyMaker, Readme
from ..models import Author, EntryPoint, RootDependency
from .base import BaseConverter


try:
    from yapf.yapflib.style import CreateGoogleStyle
    from yapf.yapflib.yapf_api import FormatCode
except ImportError:
    FormatCode = None
try:
    from autopep8 import fix_code
except ImportError:
    fix_code = None


logger = getLogger('dephell.converters.setuppy')


TEMPLATE = """
# -*- coding: utf-8 -*-

# DO NOT EDIT THIS FILE!
# This file has been autogenerated by dephell <3
# https://github.com/dephell/dephell

try:
    from setuptools import setup
except ImportError:
    from distutils.core import setup

{readme}

setup(
    long_description=readme,
    {kwargs},
)
"""


class SetupPyConverter(BaseConverter):
    lock = False

    def can_parse(self, path: Path, content: Optional[str] = None) -> bool:
        if isinstance(path, str):
            path = Path(path)
        if path.name == 'setup.py':
            return True
        if not content:
            return False
        if 'setuptools' not in content and 'distutils' not in content:
            return False
        return ('setup(' in content)

    def load(self, path) -> RootDependency:
        if isinstance(path, str):
            path = Path(path)
        path = self._make_source_path_absolute(path)
        self._resolve_path = path.parent

        info = self._execute(path=path)
        if info is None:
            with chdir(path.parent):
                info = run_setup(path.name)

        root = RootDependency(
            raw_name=self._get(info, 'name'),
            version=self._get(info, 'version') or '0.0.0',
            package=PackageRoot(
                path=self.project_path or Path(),
                name=self._get(info, 'name') or None,
            ),

            description=self._get(info, 'description'),
            license=self._get(info, 'license'),

            keywords=tuple(self._get_list(info, 'keywords')),
            classifiers=tuple(self._get_list(info, 'classifiers')),
            platforms=tuple(self._get_list(info, 'platforms')),

            python=RangeSpecifier(self._get(info, 'python_requires')),
            readme=Readme.from_code(path=path),
        )

        # links
        for key, name in (('home', 'url'), ('download', 'download_url')):
            link = self._get(info, name)
            if link:
                root.links[key] = link

        # authors
        for name in ('author', 'maintainer'):
            author = self._get(info, name)
            if author:
                root.authors += (
                    Author(name=author, mail=self._get(info, name + '_email')),
                )

        # entrypoints
        entrypoints = []
        for group, content in (getattr(info, 'entry_points', {}) or {}).items():
            for entrypoint in content:
                entrypoints.append(EntryPoint.parse(text=entrypoint, group=group))
        root.entrypoints = tuple(entrypoints)

        # dependency_links
        urls = dict()
        for url in self._get_list(info, 'dependency_links'):
            parsed = parse_link(url)
            name = parsed.name.split('-')[0]
            urls[name] = url

        # dependencies
        for req in self._get_list(info, 'install_requires'):
            req = Requirement(req)
            root.attach_dependencies(DependencyMaker.from_requirement(
                source=root,
                req=req,
                url=urls.get(req.name),
            ))

        # extras
        for extra, reqs in getattr(info, 'extras_require', {}).items():
            extra, marker = self._split_extra_and_marker(extra)
            envs = {extra} if extra == 'dev' else {'main', extra}
            for req in reqs:
                req = Requirement(req)
                root.attach_dependencies(DependencyMaker.from_requirement(
                    source=root,
                    req=req,
                    marker=marker,
                    envs=envs,
                ))

        return root

    def dumps(self, reqs, project: RootDependency, content=None) -> str:
        """
        https://setuptools.readthedocs.io/en/latest/setuptools.html#metadata
        """
        content = []
        content.append(('name', project.raw_name))
        content.append(('version', project.version))
        if project.description:
            content.append(('description', project.description))
        if project.python:
            content.append(('python_requires', str(project.python.peppify())))

        # links
        fields = (
            ('home', 'url'),
            ('download', 'download_url'),
        )
        for key, name in fields:
            if key in project.links:
                content.append((name, project.links[key]))
        if project.links:
            content.append(('project_urls', project.links))

        # authors
        if project.authors:
            author = project.authors[0]
            content.append(('author', author.name))
            if author.mail:
                content.append(('author_email', author.mail))
        if len(project.authors) > 1:
            author = project.authors[1]
            content.append(('maintainer', author.name))
            if author.mail:
                content.append(('maintainer_email', author.mail))

        if project.license:
            content.append(('license', project.license))
        if project.keywords:
            content.append(('keywords', ' '.join(project.keywords)))
        if project.classifiers:
            content.append(('classifiers', list(project.classifiers)))
        if project.platforms:
            content.append(('platforms', project.platforms))
        if project.entrypoints:
            entrypoints = defaultdict(list)
            for entrypoint in project.entrypoints:
                entrypoints[entrypoint.group].append(str(entrypoint))
            content.append(('entry_points', entrypoints))

        # packages, package_data
        content.append(('packages', sorted(str(p) for p in project.package.packages)))
        if project.package.package_dir:
            content.append(('package_dir', project.package.package_dir))
        data = defaultdict(list)
        for rule in project.package.data:
            data[rule.module].append(rule.relative)
        data = {package: sorted(paths) for package, paths in data.items()}
        content.append(('package_data', data))

        # depedencies
        reqs_list = [self._format_req(req=req) for req in reqs if not req.main_envs]
        content.append(('install_requires', reqs_list))

        # dependency_links
        links = []
        for req in reqs:
            if req.dep.link is not None:
                links.append(self._format_link(req=req))
        if links:
            content.append(('dependency_links', links))

        # extras
        extras = defaultdict(list)
        for req in reqs:
            if req.main_envs:
                formatted = self._format_req(req=req)
                for env in req.main_envs:
                    extras[env].append(formatted)
        if extras:
            content.append(('extras_require', extras))

        if project.readme is not None:
            readme = project.readme.to_rst().as_code()
        else:
            readme = "readme = ''"

        content = ',\n    '.join(
            '{}={!s}'.format(name, json_dumps(value, sort_keys=True))
            if isinstance(value, dict) else '{}={!r}'.format(name, value)
            for name, value in content)
        content = TEMPLATE.format(kwargs=content, readme=readme)

        # beautify
        if FormatCode is not None:
            content, _changed = FormatCode(content, style_config=CreateGoogleStyle())
        if fix_code is not None:
            content = fix_code(content)

        return content

    # private methods

    @staticmethod
    def _execute(path: Path):
        source = path.read_text('utf-8')
        # Remove any dotted module names
        new_source = sub(r'[a-z][a-z0-9.]*\.setup\(', 'setup(', source)
        # Remove return
        new_source = new_source.replace('return setup(', 'setup(')
        # Rename functions that end with setup
        new_source = sub(r'([_a-z][a-z0-9._]*)setup\(', r'setup\1(', new_source)
        # Ensure _dist is global
        new_source = new_source.replace('setup(', 'global _dist; _dist = dict(')
        if new_source == source:
            logger.error('cannot modify source')
            return None

        globe = {
            '__file__': str(path),
            '__name__': '__main__',
        }
        with chdir(path.parent):
            try:
                exec(compile(new_source, path.name, 'exec'), globe)
            except Exception as e:
                logger.error('{}: {}'.format(type(e).__name__, str(e)))

        dist = globe.get('_dist')
        if dist is None:
            logger.error('distribution was not called')
            return None
        return Distribution(dist)

    @staticmethod
    def _get(msg, name: str) -> str:
        value = getattr(msg.metadata, name, None)
        if not value:
            value = getattr(msg, name, None)
        if not value:
            return ''
        value = str(value)
        if value == 'UNKNOWN':
            return ''
        return value.strip()

    @staticmethod
    def _get_list(msg, name: str) -> tuple:
        if name == 'keywords':
            return ' '.join(msg.get_keywords()).split()

        getter = getattr(msg, 'get_' + name, None)
        if getter:
            values = getter()
        else:
            values = getattr(msg, name, None)
        if type(values) is str:
            values = values.split()
        if not values:
            return ()
        return tuple(value for value in values if value != 'UNKNOWN' and value.strip())

    @staticmethod
    def _format_req(req) -> str:
        line = req.raw_name
        if req.extras:
            line += '[{extras}]'.format(extras=','.join(req.extras))
        if req.version:
            line += req.version
        if req.markers:
            line += '; ' + req.markers
        return line

    @staticmethod
    def _format_link(req) -> str:
        link = req.dep.link
        egg = '#egg=' + req.name
        if req.release:
            egg += '-' + str(req.release.version)

        if isinstance(link, (FileLink, DirLink)):
            return link.short

        if isinstance(link, VCSLink):
            result = link.vcs + '+' + link.short
            if link.rev:
                result += '@' + link.rev
            return result + egg

        if isinstance(link, URLLink):
            return link.short + egg

        raise ValueError('invalid link for {}'.format(req.name))
