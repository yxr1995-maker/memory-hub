"""Match host roots against owner-configured workspace paths, without guessing."""
from pathlib import Path
from urllib.parse import urlsplit, unquote


def select_data_root(root_uris,workspaces):
    if type(root_uris) is not list or type(workspaces) is not dict:
        return None
    roots=set()
    try:
        for uri in root_uris:
            if type(uri) is not str:return None
            parsed=urlsplit(uri)
            if parsed.scheme!='file' or parsed.netloc not in ('','localhost') or parsed.query or parsed.fragment:return None
            path=Path(unquote(parsed.path))
            if not path.is_absolute() or '\x00' in str(path):return None
            roots.add(path.resolve())
        if len(roots)!=1:return None
        root=next(iter(roots));matches=[]
        for key,value in workspaces.items():
            if type(key) is not str or type(value) is not dict:continue
            workspace=Path(key).expanduser()
            if not workspace.is_absolute():continue
            workspace=workspace.resolve()
            if root.is_relative_to(workspace):matches.append((len(workspace.parts),value))
        if not matches:return None
        depth=max(d for d,_ in matches);best=[v for d,v in matches if d==depth]
        if len(best)!=1:return None
        data=best[0].get('data_path')
        if type(data) is not str or '\x00' in data:return None
        data=Path(data).expanduser()
        return data.resolve() if data.is_absolute() else None
    except (ValueError,OSError,RuntimeError):
        return None
