# api/iostore/__init__.py -- explicit re-export surface for the wiring-neutral split of the
# former api/i_o.py storage I/O layer. `from api.i_o import *` / `i_o.<name>` resolve through here.
from utils.logger import get_logger
log = get_logger(__name__)
DEBUG_ENABLED = log.is_debug()

# passthroughs the old module re-exported (kept so seams resolve through the barrel)
from api.json_proxy import read_text, write_text, S3_ENABLED, _is_s3_path
from api.manifest.resolver import resolve_path

from .fs_shims import (s3_capabilities, _s3_call, fs_exists, fs_is_file, fs_is_dir, fs_makedirs, fs_stat, fs_stat_size, fs_mkdirs, fs_iterdir, fs_walk, fs_glob_first)
from .fs_io import (fs_remove, fs_write_bytes, fs_read_bytes, fs_copy, fs_copytree, fs_open_readbin, fs_open_writebin, make_zip_stream, io_list_projects)
from .schema import (load_schema, load_override, get_noun_schema, get_verb_schema, get_adjective_schema, get_adverb_schema, get_override_schema)
from .search import (find_non_id_field_value, find_in_override_by_non_id_field_value)
from .writers import (save_schema, save_override, append_jsonl, replace_jsonl_entry, rewrite_jsonl, save_json, read_json, write_json)
from .verb_logs import (_normalize_for_psycopg, _get_objects_db_target, _table_name, get_verb_group_log_config, _json_from_db_cell, load_verb_group_log, append_to_verb_group_log, replace_in_verb_group_log, _PSYCOPG_AVAILABLE)
from .nouns import (resolve_verb_group_from_test_type, get_noun_items, _noun_key_field, put_noun_item, resolve_noun_type_from_override, resolve_run_id_to_test_type, list_verb_groups)
from .loaders import (load_data, is_file_empty, _sanitize_table_name, get_url_base, open_file)

__all__ = [
    's3_capabilities',
    '_s3_call',
    'fs_exists',
    'fs_is_file',
    'fs_is_dir',
    'fs_makedirs',
    'fs_stat',
    'fs_stat_size',
    'fs_mkdirs',
    'fs_iterdir',
    'fs_walk',
    'fs_glob_first',
    'fs_remove',
    'fs_write_bytes',
    'fs_read_bytes',
    'fs_copy',
    'fs_copytree',
    'fs_open_readbin',
    'fs_open_writebin',
    'make_zip_stream',
    'io_list_projects',
    'load_schema',
    'load_override',
    'get_noun_schema',
    'get_verb_schema',
    'get_adjective_schema',
    'get_adverb_schema',
    'get_override_schema',
    'find_non_id_field_value',
    'find_in_override_by_non_id_field_value',
    'save_schema',
    'save_override',
    'append_jsonl',
    'replace_jsonl_entry',
    'rewrite_jsonl',
    'save_json',
    'read_json',
    'write_json',
    '_normalize_for_psycopg',
    '_get_objects_db_target',
    '_table_name',
    'get_verb_group_log_config',
    '_json_from_db_cell',
    'load_verb_group_log',
    'append_to_verb_group_log',
    'replace_in_verb_group_log',
    'resolve_verb_group_from_test_type',
    'get_noun_items',
    '_noun_key_field',
    'put_noun_item',
    'resolve_noun_type_from_override',
    'resolve_run_id_to_test_type',
    'list_verb_groups',
    'load_data',
    'is_file_empty',
    '_sanitize_table_name',
    'get_url_base',
    'open_file',
    'read_text',
    'write_text',
    'S3_ENABLED',
    '_is_s3_path',
    'resolve_path',
    'log',
    'DEBUG_ENABLED',
    '_PSYCOPG_AVAILABLE',
]
