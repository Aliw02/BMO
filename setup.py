from setuptools import setup, find_packages

setup(
    name="bmo",
    version="2.0.0",
    packages=find_packages(),
    py_modules=["cli"],
    install_requires=[
        "python-telegram-bot>=20.0",
        "python-dotenv>=1.0.0",
        "httpx>=0.28.0",
        "cryptography>=42.0.0",
        "fastapi>=0.100.0",
        "uvicorn>=0.22.0",
        "mcp>=1.0.0",
        "prompt-toolkit>=3.0",
        "rich>=13.0",
        "psutil",
        "watchfiles",
        "PyNaCl>=1.5.0",
        "base58>=2.1.0",
        "websockets>=13.0"
    ],
    entry_points={
        "console_scripts": [
            "bmo=cli:main_run",
        ],
    },
)
