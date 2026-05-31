import socket
import threading

def forward(source, destination, label):
    try:
        while True:
            data = source.recv(4096)
            if not data:
                break
            print(f"--- FROM {label} ---\n{data.decode('utf-8', 'ignore')}\n-------------------")
            destination.sendall(data)
    except Exception as e:
        print(f"Connection {label} closed: {e}")
    finally:
        source.close()
        destination.close()

def start_proxy(local_port, remote_host, remote_port):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(('127.0.0.1', local_port))
    server.listen(5)
    print(f"Proxy listening on {local_port}...")
    
    while True:
        client_sock, addr = server.accept()
        print(f"Accepted connection from {addr}")
        
        remote_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        remote_sock.connect((remote_host, remote_port))
        
        t1 = threading.Thread(target=forward, args=(client_sock, remote_sock, "CLIENT"))
        t2 = threading.Thread(target=forward, args=(remote_sock, client_sock, "SERVER"))
        t1.start()
        t2.start()

if __name__ == "__main__":
    start_proxy(4098, "127.0.0.1", 4096)
