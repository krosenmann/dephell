from dephell.commands import InitCommand
import tomlkit


def test_create(tmpdir):
    config = tmpdir.join('pyproject.toml')
    task = InitCommand(['--config', str(config)])
    result = task()
    assert result is True
    assert config.check(file=1, exists=1)
    content = config.read()
    parsed = tomlkit.parse(content)
    assert '[tool.dephell.example]' in content
    assert 'from = {format = "pip"' in content

    parsed = parsed['tool']['dephell']['example']
    assert parsed['from']['format'] == 'pip'
    assert parsed['from']['path'] == 'requirements.in'
    assert parsed['to']['format'] == 'piplock'
    assert parsed['to']['path'] == 'requirements.txt'


def test_detect(tmpdir):
    tmpdir.join('requirements.txt').write('Django>=1.9\n')

    config = tmpdir.join('pyproject.toml')
    task = InitCommand(['--config', str(config)])
    result = task()
    assert result is True
    assert config.check(file=1, exists=1)
    content = config.read()
    parsed = tomlkit.parse(content)
    assert '[tool.dephell.pip]' in content
    assert 'from = {format = "pip"' in content

    parsed = parsed['tool']['dephell']['pip']
    assert parsed['from']['format'] == 'pip'
    assert parsed['from']['path'] == 'requirements.txt'
    assert parsed['to']['format'] == 'piplock'