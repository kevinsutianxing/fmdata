from setuptools import setup, find_packages

setup(
    name="fmdata",
    version="0.1.0",
    packages=find_packages(where="."),
    package_dir={"": "."},
    install_requires=[
        "pandas",
        "numpy",
        "tushare",
        "akshare",
        "fastapi",
        "uvicorn",
    ],
    entry_points={
        "console_scripts": [
            "fmdata=fmdata.cli:main",
        ],
    },
)
