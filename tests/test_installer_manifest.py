import json
from pathlib import Path
import subprocess
import sys


def test_build_manifest_from_actual_installer(tmp_path):
    root=Path(__file__).resolve().parents[1]
    exe=tmp_path/'TelegramVideoDownloader-v0.3.8-Windows-x64-Setup.exe'
    exe.write_bytes(b'MZfixture')
    output=tmp_path/'installer-latest.json'
    result=subprocess.run([sys.executable,str(root/'scripts/build-installer-manifest.py'),
        str(exe),'--version','0.3.8','--notes','Online update','--output',str(output)],capture_output=True)
    assert result.returncode==0, result.stderr
    manifest=json.loads(output.read_text())
    assert manifest['size']==9 and manifest['version']=='0.3.8'
    assert manifest['url'].endswith(exe.name)
