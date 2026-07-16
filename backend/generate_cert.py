"""
產生一份自簽 HTTPS 憑證，讓本機/區網測試時可以用 https:// 連線。

為什麼需要這個：手機瀏覽器的相機權限（getUserMedia，「連續拍照模式」用的技術）
規定一定要在 https:// 或 localhost 底下才能用，plain http://192.168.x.x 會被擋掉。
這支腳本產生的是「自簽」憑證，瀏覽器第一次連線會跳出「不安全/風險警告」，
這是正常的（因為不是公認的憑證機構發的），每台裝置手動按「進階」→「繼續前往」
接受一次就可以了。正式對外上線（不是內部區網測試）還是要用真正的憑證
（例如 Let's Encrypt），不要一直用這個自簽憑證。

用法：
    python generate_cert.py

會在同一個資料夾產生 cert.pem 和 key.pem 兩個檔案，uvicorn 啟動時指定這兩個檔案即可。
會自動把這台電腦目前偵測到的所有區網 IP、localhost、127.0.0.1 都放進憑證的有效範圍，
不需要手動指定 IP（換了 Wi-Fi、IP 變了，重跑一次這支腳本就好）。
"""

import socket
import datetime
import ipaddress

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def detect_local_ips() -> list[str]:
    """抓這台電腦目前所有看得到的區網 IPv4 位址，盡量涵蓋常見情況。"""
    ips = {"127.0.0.1"}
    try:
        # 這招不會真的送出封包，只是借助作業系統的路由表查詢本機對外會用哪個介面/IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ips)


def main():
    local_ips = detect_local_ips()
    print("偵測到的本機 IP：", ", ".join(local_ips))

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "waste4-report-system.local"),
    ])

    san_entries = [x509.DNSName("localhost")]
    for ip in local_ips:
        try:
            san_entries.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            pass

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .sign(key, hashes.SHA256())
    )

    with open("key.pem", "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))

    with open("cert.pem", "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    print("\n已產生 cert.pem 與 key.pem。")
    print("有效範圍包含：localhost, 127.0.0.1, " + ", ".join(local_ips))
    print("接下來用 start_server.bat 啟動，或手動跑：")
    print("  uvicorn main:app --reload --host 0.0.0.0 --port 8000 --ssl-keyfile key.pem --ssl-certfile cert.pem")
    print("\n手機第一次連線 https://<你的IP>:8000/upload 會跳出安全警告，")
    print("這是正常的（自簽憑證），按「進階」→「繼續前往」接受一次即可。")


if __name__ == "__main__":
    main()
