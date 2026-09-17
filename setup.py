from setuptools import setup, find_packages

setup(
    name="Demo",
    version="0.1",
    packages=find_packages(),
    install_requires=[
        "numpy",
        "pandas",
        "lxml",
        "pypowsybl"
    ],
    entry_points={
        "console_scripts": [
            "demo=demo.main:main",
        ],
    },
    python_requires=">=3.10",
)
