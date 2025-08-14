import os
import sys
import argparse
import socket
import threading
import json
import logging
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger('AnonMsg')

# ------------------------- Серверная часть -------------------------

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Многопоточный HTTP сервер"""
    daemon_threads = True
    allow_reuse_address = True  # Разрешаем повторное использование адреса

class MessengerServer:
    """Сервер мессенджера AnonMsg"""
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.clients = {}
        self.messages = []
        self.next_id = 1
        self.running = False
        
    def start(self):
        """Запуск сервера"""
        self.running = True
        
        # Пытаемся занять порт с повторными попытками
        for attempt in range(5):
            try:
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self.socket.bind((self.host, self.port))
                self.socket.listen(10)
                break
            except OSError as e:
                if e.errno == 98:  # Адрес уже используется
                    logger.warning(f"Порт {self.port} занят, попытка {attempt+1}/5")
                    time.sleep(2)  # Ждем перед повторной попыткой
                    if attempt == 4:
                        logger.error(f"Не удалось занять порт {self.port} после 5 попыток")
                        return
                else:
                    logger.error(f"Ошибка запуска сервера: {e}")
                    return
        
        logger.info(f"🚀 Сервер запущен на {self.host}:{self.port}")
        logger.info("Ожидание подключений...")
        
        try:
            while self.running:
                try:
                    client_socket, address = self.socket.accept()
                    threading.Thread(
                        target=self.handle_client,
                        args=(client_socket, address),
                        daemon=True
                    ).start()
                except socket.error:
                    if self.running:
                        logger.error("Ошибка при принятии соединения")
        except KeyboardInterrupt:
            logger.info("\nСервер остановлен")
        finally:
            self.socket.close()
            self.running = False
    
    def stop(self):
        """Остановка сервера"""
        self.running = False
        # Создаем временное соединение чтобы выйти из accept()
        try:
            temp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            temp_socket.connect((self.host, self.port))
            temp_socket.close()
        except:
            pass
    
    def handle_client(self, client_socket, address):
        """Обработка подключения клиента"""
        try:
            # Получаем никнейм
            nickname = client_socket.recv(1024).decode().strip()
            if not nickname:
                return
                
            # Регистрируем клиента
            self.clients[client_socket] = nickname
            logger.info(f"➕ {nickname} подключился ({address[0]})")
            
            # Отправляем историю сообщений
            history = json.dumps({
                "type": "history",
                "messages": self.messages[-50:]  # Последние 50 сообщений
            })
            client_socket.send(history.encode())
            
            # Уведомляем о новом пользователе
            self.broadcast({
                "type": "notification",
                "text": f"{nickname} вошёл в чат"
            }, exclude=client_socket)
            
            # Основной цикл обработки сообщений
            while self.running:
                try:
                    message = client_socket.recv(1024).decode()
                    if not message:
                        break
                        
                    try:
                        data = json.loads(message)
                        if data["type"] == "message":
                            self.process_message(data, client_socket)
                    except:
                        pass
                except (ConnectionResetError, BrokenPipeError):
                    break
                except socket.timeout:
                    continue
                    
        except Exception as e:
            logger.error(f"Ошибка клиента: {e}")
        finally:
            # Отключение клиента
            if client_socket in self.clients:
                nickname = self.clients[client_socket]
                del self.clients[client_socket]
                logger.info(f"➖ {nickname} отключился")
                self.broadcast({
                    "type": "notification",
                    "text": f"{nickname} покинул чат"
                })
            try:
                client_socket.close()
            except:
                pass
    
    def process_message(self, data, sender_socket):
        """Обработка входящего сообщения"""
        nickname = self.clients[sender_socket]
        message = {
            "id": self.next_id,
            "sender": nickname,
            "text": data["text"],
            "timestamp": data["timestamp"]
        }
        self.next_id += 1
        self.messages.append(message)
        
        # Рассылка сообщения всем клиентам
        self.broadcast({
            "type": "message",
            "message": message
        })
    
    def broadcast(self, data, exclude=None):
        """Рассылка данных всем подключенным клиентам"""
        if not self.running:
            return
            
        message = json.dumps(data)
        for client in list(self.clients.keys()):
            if client != exclude:
                try:
                    client.send(message.encode())
                except:
                    # Удаляем нерабочих клиентов
                    if client in self.clients:
                        del self.clients[client]

# ------------------------- HTTP сервер для веб-интерфейса -------------------------

class WebRequestHandler(BaseHTTPRequestHandler):
    """Обработчик HTTP запросов для веб-интерфейса"""
    
    def do_GET(self):
        """Обработка GET запросов"""
        try:
            # API для получения сообщений
            if self.path.startswith('/api/messages'):
                self.handle_api_messages()
                return
                
            # Статические файлы
            self.handle_static_files()
                
        except Exception as e:
            logger.error(f"HTTP ошибка: {e}")
            self.send_error(500, "Internal Server Error")
    
    def handle_api_messages(self):
        """Обработка API запросов для получения сообщений"""
        # Парсим параметры запроса
        query = urlparse(self.path).query
        params = parse_qs(query)
        since_id = int(params.get('since_id', [0])[0])
        
        # Фильтруем сообщения
        messages = [msg for msg in self.server.messenger.messages if msg["id"] > since_id]
        
        # Отправляем ответ
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(messages).encode())
    
    def handle_static_files(self):
        """Обслуживание статических файлов"""
        # Определяем путь к файлу
        if self.path == '/':
            filepath = 'index.html'
        else:
            filepath = self.path.lstrip('/')
            
        # Проверяем существование файла
        if not os.path.exists(filepath):
            self.send_error(404, "File Not Found")
            return
            
        # Определяем MIME тип
        if filepath.endswith(".html"):
            content_type = 'text/html'
        elif filepath.endswith(".css"):
            content_type = 'text/css'
        elif filepath.endswith(".js"):
            content_type = 'application/javascript'
        elif filepath.endswith(".png"):
            content_type = 'image/png'
        elif filepath.endswith(".jpg") or filepath.endswith(".jpeg"):
            content_type = 'image/jpeg'
        else:
            content_type = 'text/plain'
            
        # Читаем и отправляем файл
        with open(filepath, 'rb') as f:
            content = f.read()
            
        self.send_response(200)
        self.send_header('Content-type', content_type)
        self.send_header('Content-Length', len(content))
        self.end_headers()
        self.wfile.write(content)
    
    def log_message(self, format, *args):
        """Отключаем стандартное логирование запросов"""
        pass

# ------------------------- Основная программа -------------------------

def run_server(host, port, web_port):
    """Запуск сервера мессенджера и веб-интерфейса"""
    # Создаем и запускаем сервер мессенджера
    messenger = MessengerServer(host, port)
    messenger_thread = threading.Thread(target=messenger.start, daemon=True)
    messenger_thread.start()
    
    # Настраиваем и запускаем HTTP сервер для веб-интерфейса
    web_server = ThreadedHTTPServer(('0.0.0.0', web_port), WebRequestHandler)
    web_server.messenger = messenger  # Передаем ссылку на мессенджер
    web_server.allow_reuse_address = True  # Разрешаем повторное использование адреса
    
    logger.info(f"🌐 Веб-интерфейс доступен по адресу: http://localhost:{web_port}")
    logger.info("Нажмите Ctrl+C для остановки")
    
    try:
        web_server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Получен сигнал остановки")
    finally:
        # Корректно останавливаем сервер
        logger.info("Останавливаем сервер мессенджера...")
        messenger.stop()
        messenger_thread.join(timeout=5)
        
        logger.info("Останавливаем веб-сервер...")
        web_server.server_close()
        logger.info("Серверы остановлены")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='AnonMsg Messenger')
    parser.add_argument('--host', type=str, default='0.0.0.0', 
                        help='Server host (default: 0.0.0.0)')
    parser.add_argument('--port', type=int, default=65432, 
                        help='Messenger port (default: 65432)')
    parser.add_argument('--web', type=int, default=8080, 
                        help='Web interface port (default: 8080)')
    
    args = parser.parse_args()
    
    # Проверяем доступность портов
    def is_port_available(port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
                return True
            except OSError:
                return False
    
    if not is_port_available(args.port):
        logger.error(f"Порт {args.port} для мессенджера недоступен!")
        sys.exit(1)
    
    if not is_port_available(args.web):
        logger.error(f"Порт {args.web} для веб-интерфейса недоступен!")
        sys.exit(1)
    
    run_server(args.host, args.port, args.web)
