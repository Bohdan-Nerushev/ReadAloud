#!/usr/bin/env python3
import os
import sys
import subprocess
from pathlib import Path

def main():
    tests_dir = Path(__file__).parent
    test_files = []
    
    # Find all test files starting with test_
    for root, _, files in os.walk(tests_dir):
        for f in files:
            if f.startswith("test_") and f.endswith(".py"):
                full_path = Path(root) / f
                # Get module path relative to project root
                rel_path = full_path.relative_to(tests_dir.parent)
                test_files.append(rel_path)
                
    # Sort files for deterministic execution
    test_files.sort()
    
    failed = False
    passed_count = 0
    failed_count = 0
    
    print("=" * 70)
    print("RUNNING ALL READALOUD TESTS IN ISOLATED PROCESSES")
    print("=" * 70)
    
    for test_file in test_files:
        module_name = str(test_file).replace(os.path.sep, ".").replace(".py", "")
        print(f"\nRunning test module: {module_name}")
        print("-" * 70)
        
        # Run unittest as a separate process
        res = subprocess.run(
            [sys.executable, "-m", "unittest", module_name],
            cwd=str(tests_dir.parent),
            env=os.environ.copy()
        )
        
        if res.returncode == 0:
            passed_count += 1
            print(f"SUCCESS: {module_name}")
        else:
            failed_count += 1
            failed = True
            print(f"FAILED: {module_name} (exit code: {res.returncode})")
            
    print("\n" + "=" * 70)
    print("TEST EXECUTION SUMMARY")
    print("=" * 70)
    print(f"Passed modules: {passed_count}")
    print(f"Failed modules: {failed_count}")
    print("=" * 70)
    
    if failed:
        print("OVERALL STATUS: FAILED")
        sys.exit(1)
    else:
        print("OVERALL STATUS: SUCCESS")
        sys.exit(0)

if __name__ == "__main__":
    main()
