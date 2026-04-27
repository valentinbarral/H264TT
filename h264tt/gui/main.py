import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv:
        from h264tt.cli import main as cli_main

        return cli_main(argv)

    from h264tt.gui.app import launch_gui

    return launch_gui()


if __name__ == "__main__":
    main()
