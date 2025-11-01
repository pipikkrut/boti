import subprocess
import sys

processes = []

try:
    # используем тот же Python, что и у main.py
    python = sys.executable

    p1 = subprocess.Popen([python, "minichaynik.py"])
    processes.append(p1)
    print("✅ Миничайник запущен")

    p2 = subprocess.Popen([python, "chaynik.py"])
    processes.append(p2)
    print("✅ Чайник запущен")

    print("\nОба бота работают. Нажми Ctrl+C для остановки.\n")

    for p in processes:
        p.wait()

except KeyboardInterrupt:
    print("\n⏹ Остановка ботов...")
    for p in processes:
        p.terminate()
    print("Все процессы остановлены.")
