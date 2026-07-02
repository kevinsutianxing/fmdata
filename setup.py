from setuptools import find_packages, setup

setup(
    name="fmdata",
    version="1.0.0",
    packages=find_packages(where="."),
    package_dir={"": "."},
    install_requires=[
        "pandas",
        "numpy",
        "tushare",
        "akshare",
        "fastapi",
        "uvicorn",
        "PyYAML",
    ],
    entry_points={
        "console_scripts": [
            "fmdata=fmdata.cli:main",
        ],
    },
)
