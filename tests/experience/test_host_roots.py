from pathlib import Path
import pytest
from scripts.automation_core.experience.host_roots import select_data_root


def test_authorized_encoded_root_and_deepest_mapping(tmp_path):
    ws=tmp_path/'project space';nested=ws/'sub';data=tmp_path/'data';inner=tmp_path/'inner'
    mappings={str(ws):{'data_path':str(data)},str(nested):{'data_path':str(inner)}}
    assert select_data_root([ws.as_uri()],mappings)==data
    assert select_data_root([(nested/'child').as_uri()],mappings)==inner
    assert select_data_root([ws.as_uri(),ws.as_uri()],mappings)==data


@pytest.mark.parametrize('roots', [[],['https://host/project'],['file://remote/project'],['file:///project?q=1'],['file:///project#x'],['file:relative'],['file:///project','file:///other'],['file:///project/../outside']])
def test_ambiguous_or_unapproved_root_is_rejected(roots):
    assert select_data_root(roots,{'/project':{'data_path':'/data'}}) is None


def test_invalid_mapping_and_symlink_escape(tmp_path):
    ws=tmp_path/'workspace';ws.mkdir();outside=tmp_path/'outside';outside.mkdir();(ws/'link').symlink_to(outside,target_is_directory=True)
    assert select_data_root([(ws/'link').as_uri()],{str(ws):{'data_path':'/data'}}) is None
    assert select_data_root([ws.as_uri()],{str(ws):{'data_path':'relative'}}) is None
    assert select_data_root(['file://localhost'+str(ws)],{str(ws):{'data_path':'/data'}})==Path('/data')
