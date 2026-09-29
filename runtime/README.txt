Parts Manager embedded Python runtime
=====================================

This folder is reserved for the official CPython Windows embeddable package (64-bit),
Python 3.13.x.

Place the extracted contents of the official embeddable package here before building
the installer. The build script verifies runtime\pythonw.exe exists.

The application itself uses Python standard library only; no pip installation is
required.
