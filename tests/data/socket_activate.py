"""Run a command the way systemd's socket activation does: a listening
socket on fd 3, LISTEN_FDS=1 and LISTEN_PID set to the command's own pid
(exec keeps the pid). argv: port, then the command."""
import os
import socket
import sys

port = int(sys.argv[1])
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", port))
s.listen(128)
if s.fileno() != 3:
    os.dup2(s.fileno(), 3)
os.set_inheritable(3, True)
env = dict(os.environ, LISTEN_FDS="1", LISTEN_PID=str(os.getpid()))
os.execvpe(sys.argv[2], sys.argv[2:], env)
