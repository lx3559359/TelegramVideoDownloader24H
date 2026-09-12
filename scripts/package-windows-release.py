"""Build an allowlisted source-based Windows ZIP from the committed release."""
from pathlib import Path
import hashlib,json,subprocess,tomllib,zipfile
root=Path(__file__).resolve().parents[1]
def git(*args):return subprocess.check_output(['git',*args],cwd=root)
version=tomllib.loads(git('show','HEAD:pyproject.toml').decode())['project']['version']
commit=git('rev-parse','HEAD').decode().strip()
out=root/'.tmp'/'public-releases';out.mkdir(parents=True,exist_ok=True)
name=f'TelegramVideoDownloader-v{version}-Windows.zip';target=out/name
if target.exists():raise SystemExit('Release archive already exists; do not overwrite silently')
allowed=['src','tests','packaging','scripts/bootstrap.ps1','scripts/check.ps1','scripts/launch-gui.ps1','scripts/run-supervisor.ps1','scripts/apply-update.ps1','README.md','pyproject.toml','config.example.toml','打开配置器.cmd','.gitignore']
subprocess.run(['git','archive','--format=zip',f'--prefix=TelegramVideoDownloader-v{version}/','-o',str(target),'HEAD',*allowed],cwd=root,check=True)
note=f'''Telegram 视频自动下载器 v{version} — Windows 源码启动包

适用系统：Windows 10 / 11。此 ZIP 不是独立 EXE，不内置 Python。
1. 安装 Python 3.11 或更高版本，启用 Python Launcher 或添加到 PATH。
2. 将 ZIP 完整解压到有写入权限的本地目录，不要直接在压缩包内运行。
3. 双击“打开配置器.cmd”。首次启动需要联网下载和安装 Python 依赖。
4. 在账号页填写你自己的 Telegram API ID 和 API Hash，然后扫码登录。
5. 选择监听目标并保存，在运行页选择目录并启动后台。

本发布包不包含开发者凭据、登录会话、授权数据、下载记录或已下载视频。
ZIP 不含 Git 仓库历史，内置 Git 在线更新不适用于本包；升级请从官网下载新版本，解压到新目录，并参照项目文档保留个人数据。不要覆盖或删除旧目录中的 .runtime 和下载文件。
退出托盘不会停止后台，请先明确停止后台再升级。
账号授权功能和适用限制以工具界面为准。更多功能说明见 README.md。
构建来源：{commit}
'''
with zipfile.ZipFile(target,'a',compression=zipfile.ZIP_DEFLATED) as z:z.writestr(f'TelegramVideoDownloader-v{version}/安装说明.txt',note)
with zipfile.ZipFile(target) as z:
 assert z.testzip() is None
 for name_in_zip in z.namelist():
  parts=Path(name_in_zip).parts
  assert not any(p in ('.runtime','.venv','.cache','.tmp','downloads','logs','license-server','website') for p in parts),name_in_zip
  assert 'VPS' not in name_in_zip and not name_in_zip.endswith('.session'),name_in_zip
 count=len(z.namelist())
sha=hashlib.sha256(target.read_bytes()).hexdigest()
(out/(name+'.sha256')).write_text(f'{sha}  {name}\n',encoding='ascii',newline='\n')
metadata={'version':version,'filename':name,'bytes':target.stat().st_size,'sha256':sha,'commit':commit,'kind':'Windows source ZIP; requires Python 3.11+','files':count}
(out/'release.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(metadata,ensure_ascii=False))
