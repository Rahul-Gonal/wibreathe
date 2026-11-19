import os
import sys
import glob
import importlib.util

def run_all():
    test_dir = os.path.dirname(os.path.abspath(__file__))
    test_files = sorted(glob.glob(os.path.join(test_dir, "test_*.py")))
    
    total_run = 0
    total_passed = 0
    failed = []

    print("=" * 70)
    print("WiBreathe Test Suite Runner")
    print("=" * 70)

    for fpath in test_files:
        mod_name = os.path.splitext(os.path.basename(fpath))[0]
        spec = importlib.util.spec_from_file_location(mod_name, fpath)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            print(f"FAILED to import {mod_name}: {e}")
            failed.append((mod_name, str(e)))
            continue

        test_funcs = [getattr(mod, name) for name in dir(mod) if name.startswith("test_") and callable(getattr(mod, name))]
        
        for func in test_funcs:
            total_run += 1
            fname = func.__name__
            try:
                func()
                print(f"PASS: {mod_name}.{fname}")
                total_passed += 1
            except Exception as e:
                print(f"FAIL: {mod_name}.{fname} -> {e}")
                failed.append((f"{mod_name}.{fname}", str(e)))

    print("=" * 70)
    print(f"Summary: {total_passed}/{total_run} tests passed.")
    if failed:
        print(f"FAILED tests ({len(failed)}):")
        for name, err in failed:
            print(f"  - {name}: {err}")
        sys.exit(1)
    else:
        print("ALL TESTS PASSED SUCCESSFULLY!")
        print("=" * 70)

if __name__ == "__main__":
    run_all()
