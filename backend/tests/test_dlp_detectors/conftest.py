"""Re-export the pipeline helpers as test-suite fixtures.

The helpers live in ``_helpers.py`` (a plain module so it can be
imported from sibling suites — ``test_dlp_pipeline`` and
``test_dlp_regression`` — without depending on pytest fixture
resolution). This conftest only ensures the parent ``tests/`` package
is on sys.path when these subdirectories are collected.
"""
