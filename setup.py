from setuptools import setup, find_packages

setup(
    name="validation-lib",
    version="0.1.0",
    description="Business data validation library with dynamic rule loading",
    author="Jude Payne",
    packages=find_packages(),
    package_data={
        'validation_lib': ['local-config.yaml'],
        'logic': ['**/*.py', '**/*.yaml', '**/*.json'],
    },
    include_package_data=True,
    install_requires=[
        'pyyaml>=6.0',
        'jsonschema>=4.17.0',
        'requests>=2.28.0',
    ],
    python_requires='>=3.9',
)
