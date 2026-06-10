# Hermes File-Plugin Wrapper Contract

Hermes file-plugin wrappers are the small `__init__.py` files copied into
`~/.hermes/plugins`. They are not implementation modules. They exist only to
delegate from the Python process running Hermes into the installed
`hermes-antigravity-auth` package.

Every wrapper must follow one contract:

1. Import `antigravity_auth.plugin_contract` from the current Hermes Python.
2. Delegate to `load_cli_register(__file__)` or
   `load_provider_namespace(__file__)`.
3. Raise an actionable `RuntimeError` if the package, contract module, or
   delegated entrypoint cannot load.
4. Include the wrapper path, `sys.executable`, failing target, and
   `hermes-antigravity-install` repair command in loud failures.
5. Never catch and ignore import or registration failures in the wrapper file.

The wrappers intentionally contain no provider logic, CLI logic, credential
logic, Hermes monkey-patching, or fallback provider behavior. Those belong in
the installed package so `hermes-antigravity-install` can guarantee Hermes is
loading the same implementation that tests exercise.
