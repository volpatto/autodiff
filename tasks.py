from invoke import task
from invoke.exceptions import Exit
from pathlib import Path
from typing import Optional
import os
import shutil
import sys


if sys.platform.startswith('win'):
    from pathlib import WindowsPath
    # BUILD_DIR_DEFAULT = WindowsPath(build_dir_default_as_str.replace(";", ""))  # this should works, but...
    # This is a limitation since GH Actions for Win lose env variables
    # defined on environment.devenv.yml
    BUILD_DIR_DEFAULT = WindowsPath(Path(__file__).parent.absolute()) / "build-autodiff"
else:
    build_dir_default_as_str = os.environ['AUTODIFF_BUILD_DIR']
    BUILD_DIR_DEFAULT = Path(build_dir_default_as_str.replace(":", ""))


def _get_vcvars_paths():
    template = r"%PROGRAMFILES(X86)%\Microsoft Visual Studio\2019\{edition}\VC\Auxiliary\Build\vcvarsall.bat"
    template = os.path.expandvars(template)
    editions = ('BuildTools', 'Professional', 'WDExpress', 'Community')
    return tuple(Path(template.format(edition=edition)) for edition in editions)


def strip_and_join(s: str):
    return ' '.join(line.strip() for line in s.splitlines() if line.strip() != '')


def echo(c, msg: str):
    from colorama.ansi import Fore, Style
    if c.config.run.echo:
        print(f"{Fore.WHITE}{Style.BRIGHT}{msg}{Style.RESET_ALL}")


def remove_directory(path: Path):
    if path.is_dir():
        print(f"Removing {path}")
        shutil.rmtree(path)
    else:
        print(f"Not removing {path} (not a directory)")


def _get_and_prepare_build(
        c,
        clean: bool = False,
        build_subdirectory: Path = BUILD_DIR_DEFAULT
) -> Path:
    '''
    Returns build directory where `cmake` shall be called from. Creates it and
    possibly removes its contents (and artifacts_dir contents) if `clean=True`
    is passed.
    '''
    build_dir = build_subdirectory
    if clean:
        remove_directory(build_dir)
    build_dir.mkdir(parents=True, exist_ok=not clean)
    return build_dir


def _get_cmake_config_command(
        build_dir: Path,
        cmake_generator: str,
        cmake_arch: Optional[str] = None,
        config: str = 'Release',
):
    '''
    :param build_dir: Directory from where cmake will be called.
    '''
    root_dir = Path(__file__).parent
    relative_root_dir = Path(os.path.relpath(root_dir, build_dir))
    relative_artifacts_dir = Path(os.path.relpath(build_dir))

    if sys.platform.startswith('win'):
        cmake_command = strip_and_join(f"""
            cmake
                -G "{cmake_generator}"
                -S {root_dir}
                -B {BUILD_DIR_DEFAULT}
        """)
    else:
        cmake_command = strip_and_join(f"""
            cmake
                -G "{cmake_generator}"
                {f'-A "{cmake_arch}"' if cmake_arch is not None else ""}
                -DCMAKE_BUILD_TYPE={config}
                -DCMAKE_INSTALL_PREFIX="{relative_artifacts_dir.as_posix()}"
                "{str(relative_root_dir)}"
        """)

    return cmake_command


def _get_cmake_build_command(build_dir: Path, config: str = 'Release', number_of_jobs: int = -1):
    build_command = strip_and_join(f"""
        cmake
            --build {build_dir}
            --target install
            --config {config}
            --
                {f"-j {number_of_jobs}" if number_of_jobs >= 1 else ""}
    """)
    return build_command


def _get_wrappers_command(c, wrappers_dir: Path) -> str:
    conda_prefix = os.environ['CONDA_PREFIX']
    if sys.platform.startswith('win'):
        autodiff_env_path = f"{conda_prefix}\\Library\\bin"
    else:
        autodiff_env_path = f"{conda_prefix}/bin"
    return strip_and_join(f"""
        create-wrappers
            -t conda
            --bin-dir {autodiff_env_path}
            --dest-dir {wrappers_dir}
            --conda-env-dir {conda_prefix}
    """)


def _get_test_command(config: str = 'Release'):
    if sys.platform.startswith('win'):
        test_command = strip_and_join(f"""
            {BUILD_DIR_DEFAULT}\\test\\{config}\\tests
                --success
                --reporter compact
        """)
    else:
        test_command = strip_and_join(f"""
            {BUILD_DIR_DEFAULT}/test/tests
                --success
                --reporter compact
        """)
    return test_command


if sys.platform.startswith('win'):
    @task
    def msvc(c, clean=False, config='Release'):
        """
        Generates a Visual Studio project at the "build/msvc" directory.
        Assumes that the environment is already configured using:
            conda devenv
            activate autodiff
        """
        build_dir = _get_and_prepare_build(
            c,
            clean=clean,
            build_subdirectory=BUILD_DIR_DEFAULT / "msvc",
        )
        cmake_command = _get_cmake_command(build_dir=build_dir, cmake_generator="Visual Studio 16 2019",
                                           cmake_arch="x64", config=config)
        c.run(cmake_command)


@task
def compile(c, clean=False, config='Release', number_of_jobs=-1, gen_wrappers=False):
    """
    Compiles autodiff by running CMake and building with `ninja`.
    Assumes that the environment is already configured using:
        conda devenv
        [source] activate autodiff
    """
    build_dir = _get_and_prepare_build(
        c,
        clean=clean,
        build_subdirectory=BUILD_DIR_DEFAULT,
    )
    echo(c, f"autodiff build directory: {build_dir}")

    if sys.platform.startswith('win'):
        cmake_generator = "Visual Studio 16 2019"
    else:
        cmake_generator = "Ninja"

    cmake_command = _get_cmake_config_command(build_dir=build_dir, cmake_generator=cmake_generator, config=config)
    build_command = _get_cmake_build_command(build_dir=build_dir, config=config, number_of_jobs=number_of_jobs)

    commands = [cmake_command, build_command]
    if gen_wrappers:
        wrappers_command = _get_wrappers_command(build_dir / "wrappers/conda")
        commands.append(wrappers_command)

    if sys.platform.startswith('win'):
        use_pty = False
    else:
        use_pty = True
        os.chdir(BUILD_DIR_DEFAULT)
    c.run(" && ".join(commands), pty=use_pty)


@task
def clear(c, build_dir_path=BUILD_DIR_DEFAULT):
    """
    Clear autodiff build directory
    """
    remove_directory(build_dir_path)


@task
def wrappers(c, wrappers_dir=BUILD_DIR_DEFAULT / "wrappers/conda"):
    """
    Wrappers bin generated by autodiff conda environment as passed with --wrappers-dir dir_path
    """
    remove_directory(wrappers_dir)
    if sys.platform.startswith('win'):
        print(f"Generating conda wrappers to {wrappers_dir} from {os.environ['CONDA_PREFIX']}\\Library\\bin")
    else:
        print(f"Generating conda wrappers to {wrappers_dir} from {os.environ['CONDA_PREFIX']}/bin")

    generate_wrappers_command = _get_wrappers_command(c, wrappers_dir)
    use_pty = True
    if sys.platform.startswith('win'):
        use_pty = False
    c.run(generate_wrappers_command, pty=use_pty, warn=True)


@task
def tests(c, config='Release'):
    """
    Execute autodiff tests in Catch
    """
    test_command = _get_test_command(config)
    use_pty = True
    if sys.platform.startswith('win'):
        use_pty = False
    c.run(test_command, pty=use_pty)
