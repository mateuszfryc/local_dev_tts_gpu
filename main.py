import logging

from src.runtime import (
    acquire_single_instance,
    ensure_supported_python,
    install_console_ctrl_handler,
    prepare_runtime_dependencies,
    setup_logging,
    uninstall_console_ctrl_handler,
)


def main() -> int:
    if not acquire_single_instance():
        return 0
    setup_logging()
    logging.info("main start")
    ensure_supported_python()
    prepare_runtime_dependencies()
    from src.app import DictationApp

    app = DictationApp()
    install_console_ctrl_handler(app)
    try:
        app.run()
    except KeyboardInterrupt:
        logging.info("KeyboardInterrupt received")
        app.exit_app()
    except Exception:
        logging.exception("unhandled exception in main")
        raise
    finally:
        uninstall_console_ctrl_handler()
        if app.restart_requested:
            app.start_replacement_process()
    logging.info("main end")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
