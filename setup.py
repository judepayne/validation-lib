from setuptools import setup, find_packages
import os

# Get all files in logic/ directory recursively
def get_logic_files():
    logic_files = []
    for root, dirs, files in os.walk('logic'):
        for file in files:
            # Get path relative to package root
            path = os.path.join(root, file)
            logic_files.append(path)
    return logic_files

setup(
    name="validation-lib",
    version="0.1.0",
    description="Business data validation library with dynamic rule loading",
    author="Jude Payne",
    packages=find_packages(),
    package_data={
        '': ['local-config.yaml', 'coordination-service-config.yaml'] + get_logic_files(),
    },
    include_package_data=True,
    install_requires=[
        'pyyaml>=6.0',
        'jsonschema>=4.17.0',
        'requests>=2.28.0',
    ],
    python_requires='>=3.9',
)
