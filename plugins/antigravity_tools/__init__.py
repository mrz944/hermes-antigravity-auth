from antigravity_auth.cli import setup_cli, handle_cli

def register(ctx):
    ctx.register_cli_command(
        name="antigravity",
        help="Google Antigravity utilities",
        setup_fn=setup_cli,
        handler_fn=handle_cli
    )
