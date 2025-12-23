---
❌ Error running parser: micro_test_parser
Date: 2025-06-26 21:18
Traceback:

```
Traceback (most recent call last):
  File "GIMS-Project/utils/data_dump.py", line 448, in run_parsers_with_error_handling
    success = execute_parser_runner(project_path, run_id)
              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "GIMS-Project/utils/runner_env.py", line 429, in execute_parser_runner
    raise RuntimeError(f"❌ No parser in docker/ matched verb '{verb_group}', or missing entrypoint")
RuntimeError: ❌ No parser in docker/ matched verb 'Tests', or missing entrypoint
```
---
❌ Error running parser: micro_test_parser
Date: 2025-06-26 21:20
Traceback:

```
Traceback (most recent call last):
  File "GIMS-Project/utils/data_dump.py", line 448, in run_parsers_with_error_handling
    success = execute_parser_runner(project_path, run_id)
              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "GIMS-Project/utils/runner_env.py", line 408, in execute_parser_runner
    raise RuntimeError("❌ test_type or verb not defined in Status.json")
RuntimeError: ❌ test_type or verb not defined in Status.json
```
---
❌ Error running parser: micro_test_parser
Date: 2025-06-26 21:24
Traceback:

```
Traceback (most recent call last):
  File "GIMS-Project/utils/data_dump.py", line 448, in run_parsers_with_error_handling
    success = execute_parser_runner(project_path, verb_key, run_id)
                                                  ^^^^^^^^
NameError: name 'verb_key' is not defined
```
---
❌ Error running parser: micro_test_parser
Date: 2025-06-26 21:55
Traceback:

```
Traceback (most recent call last):
  File "GIMS-Project/utils/data_dump.py", line 458, in run_parsers_with_error_handling
    raise RuntimeError("One or more parsers failed (unknown reason)")
RuntimeError: One or more parsers failed (unknown reason)
```
---
❌ Error running parser: micro_test_parser
Date: 2025-06-27 16:38
Traceback:

```
Traceback (most recent call last):
  File "GIMS-Project/utils/data_dump.py", line 465, in run_parsers_with_error_handling
    raise RuntimeError("One or more parsers failed (unknown reason)")
RuntimeError: One or more parsers failed (unknown reason)
```
---
❌ Error running parser: micro_test_parser
Date: 2025-06-27 17:25
Traceback:

```
Traceback (most recent call last):
  File "GIMS-Project/utils/data_dump.py", line 465, in run_parsers_with_error_handling
    raise RuntimeError("One or more parsers failed (unknown reason)")
RuntimeError: One or more parsers failed (unknown reason)
```
