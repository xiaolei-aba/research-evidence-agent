"""Own one portable process; monitor actual device usage, temperature/RAM, and terminate only its handle."""
from pathlib import Path
from hashlib import sha256
from datetime import datetime, timezone
import ctypes
import json
import os
import socket
import subprocess
import threading
import time
from urllib.request import Request, urlopen
from uuid import uuid4
from deepresearch.runtime.process_memory import system_commit, process_memory
from deepresearch.benchmarks.adapters import canonical_hash


def backend_execution_configuration(artifact, context=4096):
    return {'backend_release': artifact['backend_release'], 'backend_files': artifact['backend_files'],
            'weight_sha256': artifact['weight_sha256'], 'context': context,
            'launch_options': ['--alias', 'research-qwen', '-ngl', '99', '-c', str(context), '-np', '1',
                '-b', '512', '-ub', '128', '-t', '4', '--flash-attn', 'on', '--no-mmap',
                '--cache-type-k', 'f16', '--cache-type-v', 'f16', '--no-context-shift', '--jinja',
                '--no-webui', '--cache-ram', '0'],
            'bind_host': '127.0.0.1', 'request_cache_prompt': False,
            'cache_ram_mib': 0,
            'ephemeral_port_and_credentials_excluded': True}


def hardware_sample():
    class Memory(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
            (n, ctypes.c_ulonglong) for n in ('total_phys', 'avail_phys', 'total_page', 'avail_page',
                                           'total_virtual', 'avail_virtual', 'avail_extended')]
    mem = Memory(); mem.length = ctypes.sizeof(mem)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)): raise OSError('memory_probe_failed')
    raw = subprocess.run(['nvidia-smi', '--query-gpu=memory.used,memory.free,temperature.gpu,utilization.gpu',
                          '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=8,
                         creationflags=subprocess.CREATE_NO_WINDOW, check=True).stdout.strip().split(',')
    used, free, temp, util = [int(v.strip()) for v in raw]
    processes = subprocess.run(['nvidia-smi', '--query-compute-apps=pid', '--format=csv,noheader,nounits'],
                               capture_output=True, text=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW,
                               check=True).stdout.splitlines()
    return {'utc': datetime.now(timezone.utc).isoformat(), 'memory_used_mib': used, 'memory_free_mib': free,
            'temperature_c': temp, 'gpu_utilization_percent': util, 'ram_available_bytes': mem.avail_phys,
            'gpu_pids': [int(s.strip()) for s in processes if s.strip().isdigit()], **system_commit()}


class OwnedServer:
    def __init__(self, root, context=4096, artifact_path='results/runtime_preflight/artifact_acquisition.json'):
        self.root = Path(root).resolve(); self.context = context; self.process = None; self.samples = []
        self.guard_error = None; self.stop_event = threading.Event(); self.lock = threading.RLock()
        self.baseline_pids = set(hardware_sample()['gpu_pids']); self.thread = None
        self.started = None; self.load_ms = None
        self.artifact_path=artifact_path
        self.workers = {}

    def register_worker(self, child):
        with self.lock: self.workers[child.pid] = child

    def unregister_worker(self, child):
        with self.lock: self.workers.pop(child.pid, None)

    def start(self):
        if self.process is not None and self.process.poll() is not None: self.stop()
        with self.lock:
            if self.process is not None and self.process.poll() is None: return
            sample = hardware_sample()
            if sample['temperature_c'] >= 80 or sample['memory_free_mib'] < 4096 or sample['ram_available_bytes'] < 1_342_177_280:
                raise RuntimeError('unsafe_preload_resource_state')
            if set(sample['gpu_pids']) - self.baseline_pids: raise RuntimeError('new_foreign_gpu_process_before_load')
            artifact = json.loads((self.root / self.artifact_path).read_text())
            if artifact['status'] != 'all_artifact_bytes_sha_verified': raise RuntimeError('artifacts_not_verified')
            binary = next((self.root / '.local/backend').rglob('llama-server.exe'))
            model = self.root / '.local/models' / artifact['weight_file']
            if model.stat().st_size != artifact['weight_bytes']: raise RuntimeError('weight_size_changed')
            if sha256(binary.read_bytes()).hexdigest() != artifact['backend_files'][binary.relative_to(self.root).as_posix()]:
                raise RuntimeError('backend_binary_changed')
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0)); self.port = probe.getsockname()[1]
            self.base_url = f'http://127.0.0.1:{self.port}'
            if not os.environ.get('DEEPRESEARCH_LOCAL_API_KEY'): os.environ['DEEPRESEARCH_LOCAL_API_KEY'] = uuid4().hex
            env = dict(os.environ); env['LLAMA_API_KEY'] = os.environ['DEEPRESEARCH_LOCAL_API_KEY']
            env['PATH'] = str(binary.parent) + os.pathsep + env.get('PATH', '')  # Child only, never global PATH.
            self.execution_configuration = backend_execution_configuration(artifact, self.context)
            self.backend_fingerprint = canonical_hash(self.execution_configuration)
            args = [str(binary), '-m', str(model), '--host', '127.0.0.1', '--port', str(self.port),
                    *self.execution_configuration['launch_options']]
            out = self.root / 'results/runtime_preflight/server_logs'; out.mkdir(parents=True, exist_ok=True)
            self.server_log = out / (uuid4().hex + '.log')
            self.handle = self.server_log.open('wb'); self.started = time.monotonic()
            self.process = subprocess.Popen(args, cwd=binary.parent, env=env, stdout=self.handle, stderr=subprocess.STDOUT,
                                            creationflags=subprocess.CREATE_NO_WINDOW)
            self.stop_event.clear(); self.guard_error = None
            self.thread = threading.Thread(target=self._monitor, daemon=True); self.thread.start()
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if self.guard_error: self.stop(); raise RuntimeError(self.guard_error)
            if self.process.poll() is not None: raise RuntimeError('owned_backend_exited; see ' + str(self.server_log))
            try:
                with urlopen(Request(self.base_url + '/health', headers={'Authorization': 'Bearer ' + os.environ['DEEPRESEARCH_LOCAL_API_KEY']}), timeout=2) as response:
                    if response.status == 200:
                        self.load_ms = (time.monotonic() - self.started) * 1000
                        self._snapshot(args, artifact); return
            except Exception: pass
            time.sleep(.5)
        self.stop(); raise TimeoutError('backend_load_deadline')

    def _monitor(self):
        path = self.root / 'results/runtime_preflight/resource_samples.jsonl'
        while not self.stop_event.is_set():
            try:
                sample = hardware_sample(); sample['owned_pid'] = self.process.pid
                with self.lock:
                    owned = [('parent', os.getpid()), ('backend', self.process.pid)] + [
                        ('worker', child.pid) for child in self.workers.values() if child.poll() is None]
                sample['project_processes'] = [process_memory(pid, role) for role, pid in owned]
                sample['backend_fingerprint'] = self.backend_fingerprint
                self.samples.append(sample)
                with path.open('a', encoding='utf-8') as stream: stream.write(json.dumps(sample) + '\n')
                if sample['temperature_c'] >= 82: self.guard_error = 'gpu_temperature_safety_threshold'
                if sample['memory_free_mib'] < 1024: self.guard_error = 'vram_headroom_safety_threshold'
                if sample['ram_available_bytes'] < 1_342_177_280: self.guard_error = 'ram_headroom_safety_threshold'
                if set(sample['gpu_pids']) - self.baseline_pids - {self.process.pid}: self.guard_error = 'foreign_gpu_process_started'
                if self.guard_error:
                    self.terminate_owned(); return
            except Exception as exc:
                self.guard_error = 'resource_probe_failed:' + type(exc).__name__; self.terminate_owned(); return
            self.stop_event.wait(2)

    def _snapshot(self, args, artifact):
        with urlopen(Request(self.base_url + '/props', headers={'Authorization': 'Bearer ' + os.environ['DEEPRESEARCH_LOCAL_API_KEY']}), timeout=5) as response:
            properties = json.load(response)
        # Server properties are scrubbed with the existing project redactor before persistence.
        from deepresearch.utils.redaction import redact, environment_secrets
        clean_properties = redact(properties, environment_secrets())
        private = self.root / '.local/model_loads'; private.mkdir(parents=True, exist_ok=True)
        private_path = private / (uuid4().hex + '.json')
        private_path.write_text(json.dumps(clean_properties, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        template = properties.get('chat_template', '')
        record = {'artifact': artifact, 'launch_arguments': args,
                  'backend_execution_configuration': self.execution_configuration,
                  'backend_fingerprint': self.backend_fingerprint,
                  'properties_snapshot_path': private_path.relative_to(self.root).as_posix(),
                  'properties_sha256': sha256(private_path.read_bytes()).hexdigest(),
                  'chat_template_sha256': sha256(str(template).encode()).hexdigest(),
                  'load_ms': self.load_ms, 'context': self.context, 'owned_pid': self.process.pid,
                  'device_measurement': 'nvidia-smi total device memory including pre-existing desktop use; no PyTorch allocation claims'}
        snapshots = self.root / 'results/runtime_preflight/model_loads'; snapshots.mkdir(parents=True, exist_ok=True)
        self.model_lock_path = snapshots / (uuid4().hex + '.json')
        self.model_lock_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def terminate_owned(self):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try: self.process.wait(timeout=10)
                except subprocess.TimeoutExpired: self.process.kill(); self.process.wait(timeout=5)

    def stop(self):
        self.stop_event.set(); self.terminate_owned()
        if self.thread and self.thread is not threading.current_thread(): self.thread.join(timeout=10)
        if getattr(self, 'handle', None): self.handle.close()

    def __enter__(self): self.start(); return self
    def __exit__(self, *args): self.stop()
