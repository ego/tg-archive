import os
from setuptools import setup


def get_version():
    path = os.path.join("tgarchive", "__init__.py")
    with open(path) as fd:
        for line in fd:
            if line.startswith("__version__"):
                return line.split("=")[-1].strip().replace('"', "")
        return ""


def parse_readme(file_with_readme):
    with open(file_with_readme) as fd:
        return fd.read()


def parse_requirements(file_with_requires):
    requirements = []
    with open(file_with_requires) as fd:
        for line in fd:
            if line.startswith("-e ") or line.startswith("#"):
                continue
            requirements.append(line.strip())
    return requirements


setup(
    name="tg-archive",
    description="is a tool for exporting Telegram group chats into static websites, preserving the chat history like mailing list archives.",
    long_description=parse_readme("README.md"),
    long_description_content_type="text/markdown",
    author="Kailash Nadh",
    author_email="",
    url="",
    packages=["tgarchive"],
    install_requires=parse_requirements("requirements.txt"),
    version=get_version(),
    include_package_data=True,
    download_url="",
    license="MIT License",
    entry_points={
        "console_scripts": ["tg-archive = tgarchive:main"],
    },
    classifiers=[
        "Topic :: Communications :: Chat",
        "Topic :: Internet :: WWW/HTTP :: Site Management",
        "Topic :: Documentation",
    ],
)
