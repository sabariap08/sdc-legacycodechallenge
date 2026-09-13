import subprocess
import sys
import os

def main():
    backend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend")
    print("=" * 60)
    print("  Legacy Code Rescue - Admin Challenge Management Platform")
    print("=" * 60)
    print()
    print("Starting server...")
    print("Admin Login:  http://localhost:18000/login")
    print()
    print("Default admin credentials:")
    print("  Username: admin")
    print("  Password: admin@123")
    print()

    subprocess.run(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "18000", "--reload"],
        cwd=backend_dir
    )

if __name__ == "__main__":
    main()
