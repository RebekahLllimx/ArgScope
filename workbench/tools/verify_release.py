"""Extract and exercise the actual release: library, papers, feedback and restart.

All model providers in this verifier are explicitly authored; no inference calls.
"""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path, PurePosixPath
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
import zipfile
from check_release import digest, validate

ROOT = Path(__file__).resolve().parents[2]


def extract(archive, destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    roots = set()
    names = set()
    with zipfile.ZipFile(archive) as zipped:
        if sum(info.file_size for info in zipped.infolist()) > 100_000_000:
            raise ValueError('Release exceeds extraction budget')
        for info in zipped.infolist():
            path = PurePosixPath(info.filename)
            if path.is_absolute() or '..' in path.parts or len(path.parts) < 2 or '\\' in info.filename:
                raise ValueError('Invalid archive path')
            if info.filename in names or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError('Duplicate or symlink archive member')
            names.add(info.filename)
            roots.add(path.parts[0])
            if info.is_dir():
                continue
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zipped.read(info))
            # ZIP extraction APIs otherwise lose executable bits on Unix.
            target.chmod(0o755 if (info.external_attr >> 16) & 0o111 else 0o644)
    if len(roots) != 1:
        raise ValueError('Release needs one root directory')
    return destination / roots.pop()


def stop(process):
    if process is not None and process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def verify(archive, output, python=sys.executable):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    result = {'archive_sha256': digest(Path(archive)), 'model_calls': 0, 'human_review': 'synthetic fixtures only'}
    server = None
    logs = []
    # Always discard inherited project import paths and keys from this offline verification.
    env = {k: v for k, v in os.environ.items() if k not in {'PYTHONPATH', 'OPENAI_API_KEY', 'CODEX_API_KEY', 'BAILI_API_KEY', 'DASHSCOPE_API_KEY'}}
    env.update(PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')
    try:
        with tempfile.TemporaryDirectory(prefix='argscope-release-') as temp, ExitStack() as cleanup:
            cleanup.callback(lambda: stop(server))
            temp = Path(temp)
            delivery = extract(archive, temp / 'extracted')
            result['manifest'] = validate(delivery)
            before = json.loads((delivery / 'manifest.json').read_text())['members']
            # This is the package bootstrap, not an import from the development checkout.
            help_result = subprocess.run([str(python), '-I', str(delivery / 'launch.py'), '--help'], cwd=temp, env=env, capture_output=True, text=True, timeout=15)
            assert help_result.returncode == 0, help_result.stderr
            result['bootstrap_help'] = True
            if sys.platform == 'darwin':
                subprocess.run(['/bin/zsh', '-n', str(delivery / 'Launch.command')], check=True)
            def launch(mode, data):
                port = free_port()
                log = (output / f'app-{len(logs)}.log').open('wb'); logs.append(log)
                command = [str(python), '-I', str(delivery / 'launch.py'), '--provider', mode, '--data-dir', str(data), '--port', str(port)]
                process = subprocess.Popen(command, cwd=temp, env=env, stdout=log, stderr=log)
                try:
                    for _ in range(200):
                        try:
                            with urlopen(f'http://127.0.0.1:{port}/api/argument-health', timeout=.3) as response:
                                health = json.load(response)
                            assert health['version'] == result['manifest']['version']
                            assert health['kv_available'] and not health['provider']['live_model']
                            return process, f'http://127.0.0.1:{port}'
                        except OSError:
                            if process.poll() is not None:
                                raise RuntimeError('Packaged process exited; inspect app log')
                            time.sleep(.05)
                    raise RuntimeError('Packaged startup deadline')
                except BaseException:
                    stop(process)
                    raise
            def browser(script, base, destination, extra=None):
                destination.mkdir(exist_ok=True)
                child_env = {**env, 'REVIEW_TEST_URL': base, 'REVIEW_ARTIFACTS': str(destination), 'REVIEW_SCREENSHOTS': str(destination), **(extra or {})}
                # Browser driver is test infrastructure; all pages/API/source run from extracted release.
                done = subprocess.run(['node', str(ROOT / 'workbench/tests' / script)], env=child_env, cwd=temp, capture_output=True, text=True, timeout=150)
                with (destination / (script + ('-restart' if extra else '') + '.log')).open('w') as log:
                    log.write(done.stdout + done.stderr)
                if done.returncode:
                    try:
                        with urlopen(base + '/api/argument-runs', timeout=3) as response:
                            runs = json.load(response)
                        states = []
                        for row in runs[:10]:
                            with urlopen(base + '/api/argument-runs/' + row['id'], timeout=3) as response:
                                states.append(json.load(response))
                        (destination / 'failure-states.json').write_text(json.dumps(states, indent=2))
                    except Exception as diagnostic_error:
                        (destination / 'diagnostic-error.log').write_text(str(diagnostic_error))
                    raise RuntimeError('Browser verification failed: ' + script)
                return json.loads(done.stdout)
            server, base = launch('demo', temp / 'library-data')
            result['library'] = browser('browser_release_library.cjs', base, output / 'library')
            result['interface'] = browser('browser_interface.cjs', base, output / 'interface')
            stop(server); server = None
            server, base = launch('demo', temp / 'status-data')
            result['status_order'] = browser('browser_status_order.cjs', base, output / 'status-order')
            stop(server); server = None
            server, base = launch('demo', temp / 'demo-data')
            for route in ['/', '/library']:
                with urlopen(base + route) as response:
                    assert response.status == 200
            result['baseline'] = browser('browser_review_mvp.cjs', base, output / 'baseline')
            stop(server); server = None
            server, base = launch('demo', temp / 'demo-data')
            result['baseline_restart'] = browser('browser_review_mvp.cjs', base, output / 'baseline', {'REVIEW_RESTART_RUN': result['baseline']['paused_run']})
            stop(server); server = None
            server, base = launch('papers', temp / 'papers-data')
            result['papers'] = browser('browser_first_order.cjs', base, output / 'papers')
            stop(server); server = None
            server, base = launch('demo', temp / 'feedback-data')
            result['feedback'] = browser('browser_feedback.cjs', base, output / 'feedback')
            stop(server); server = None
            server, base = launch('demo', temp / 'feedback-data')
            result['feedback_restart'] = browser('browser_feedback.cjs', base, output / 'feedback', {'FEEDBACK_RESTART_ID': result['feedback']['run_id']})
            stop(server); server = None
            packets = [output / 'baseline/argument-review.json', output / 'feedback/feedback-report.json', *(output / 'papers').glob('*-test-packet.json')]
            for packet in packets:
                completed = subprocess.run([str(python), '-I', str(delivery / 'launch.py'), 'verify-report', str(packet)], env=env, cwd=temp, capture_output=True, text=True, timeout=15)
                assert completed.returncode == 0, completed.stderr
                assert json.loads(completed.stdout)['status'] == 'snapshot_verified'
            result['cli_replays'] = len(packets)
            # Managed KV is restarted with each app; the final exact file check covers runtime code too.
            assert before == {name: digest(delivery / name) for name in before}
            result['release_source_unchanged'] = True
            result['library_page'] = 'passed'
            result['status'] = 'passed'
    except Exception as error:
        result.update(status='failed', error=str(error))
    finally:
        stop(server)
        for log in logs:
            log.close()
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--python', default=sys.executable, type=Path)
    a = p.parse_args()
    raise SystemExit(verify(a.archive.resolve(), a.output, a.python.absolute()))
