from setuptools import setup, find_packages

with open("requirements.txt") as f:
    requirements = f.read().splitlines()

setup(
    name="digihealth-lamp",
    version="1.0.0",
    description="Smart Lamp with environmental monitoring and InfluxDB integration",
    author="Your Name",
    packages=find_packages(),
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "digihealth-lamp=digihealth.main:main",
        ],
    },
    include_package_data=True,
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: POSIX :: Linux",
    ],
)