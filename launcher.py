import ctypes
import json
import logging
import os
from pathlib import Path
import socket
import sys
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser


APP_NAME = 'GiaiTichHUST'
WINDOW_TITLE = 'Giải Tích HUST'
DEFAULT_PORT = 8765
INSTANCE_LOCK_PORT = 47653


def resource_dir():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)).resolve()


def application_data_dir():
    configured = os.getenv('GIAITICH_APP_DATA_DIR', '').strip()
    if configured:
        path = Path(configured).expanduser().resolve()
    else:
        local_app_data = os.getenv('LOCALAPPDATA', '').strip()
        root = Path(local_app_data) if local_app_data else (Path.home() / 'AppData' / 'Local')
        path = (root / APP_NAME).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def show_error(message):
    logging.error(message)
    if os.name == 'nt' and not os.getenv('GIAITICH_HEADLESS'):
        ctypes.windll.user32.MessageBoxW(0, str(message), WINDOW_TITLE, 0x10)


def configure_runtime(data_dir):
    os.environ.setdefault('GIAITICH_APP_DATA_DIR', str(data_dir))
    os.environ.setdefault('FLASK_DEBUG', '0')
    os.environ.setdefault('APP_HOST', '127.0.0.1')
    os.chdir(resource_dir())

    log_handlers = [
        logging.FileHandler(data_dir / 'application.log', encoding='utf-8'),
    ]
    if not getattr(sys, 'frozen', False):
        log_handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
        handlers=log_handlers,
    )


def acquire_instance_lock(data_dir):
    lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock_socket.bind(('127.0.0.1', INSTANCE_LOCK_PORT))
        lock_socket.listen(1)
        return lock_socket
    except OSError:
        lock_socket.close()
        state_path = data_dir / 'server.json'
        try:
            state = json.loads(state_path.read_text(encoding='utf-8'))
            port = int(state.get('port'))
            webbrowser.open(f'http://127.0.0.1:{port}/')
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            show_error('Ứng dụng đang chạy nhưng không mở lại được cửa sổ hiện tại.')
        return None


def find_available_port(preferred_port):
    for port in range(preferred_port, preferred_port + 25):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(('127.0.0.1', port))
            return port
        except OSError:
            continue
        finally:
            probe.close()
    raise RuntimeError('Không tìm được cổng localhost trống để chạy ứng dụng.')


def wait_until_ready(url, timeout=30):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return
        except (OSError, URLError) as exc:
            last_error = exc
        time.sleep(0.2)
    raise RuntimeError(f'Máy chủ nội bộ không khởi động kịp: {last_error}')


def main():
    data_dir = application_data_dir()
    configure_runtime(data_dir)
    instance_lock = acquire_instance_lock(data_dir)
    if instance_lock is None:
        return 0

    state_path = data_dir / 'server.json'
    server = None
    try:
        from waitress import create_server
        from app import app

        preferred_port = int(os.getenv('APP_PORT', str(DEFAULT_PORT)))
        port = find_available_port(preferred_port)
        url = f'http://127.0.0.1:{port}/'
        state_path.write_text(
            json.dumps({'port': port, 'pid': os.getpid()}, ensure_ascii=False),
            encoding='utf-8',
        )
        server = create_server(app, host='127.0.0.1', port=port, threads=8)

        if os.getenv('GIAITICH_HEADLESS', '0') == '1' and '--self-test' not in sys.argv:
            logging.info('Starting headless server at %s', url)
            server.run()
            return 0

        server_thread = threading.Thread(target=server.run, name='waitress-server', daemon=True)
        server_thread.start()
        wait_until_ready(url + 'login')
        wait_until_ready(url + 'static/css/style.css')

        if '--self-test' in sys.argv:
            import webview
            logging.info('Packaged self-test passed with pywebview %s', getattr(webview, '__version__', 'unknown'))
            return 0

        try:
            import webview
        except ImportError:
            logging.warning('pywebview is unavailable; opening the system browser instead.')
            webbrowser.open(url)
            server_thread.join()
            return 0

        webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = True
        webview.create_window(
            WINDOW_TITLE,
            url=url,
            width=1440,
            height=900,
            min_size=(1024, 680),
            text_select=True,
        )
        webview.start(debug=False)
        return 0
    except Exception as exc:
        logging.exception('Application startup failed')
        show_error(f'Không thể khởi động ứng dụng.\n\n{exc}\n\nXem log tại: {data_dir / "application.log"}')
        return 1
    finally:
        if server is not None:
            try:
                server.close()
            except Exception:
                logging.exception('Could not close Waitress cleanly')
        try:
            state_path.unlink(missing_ok=True)
        except OSError:
            pass
        instance_lock.close()


if __name__ == '__main__':
    raise SystemExit(main())
