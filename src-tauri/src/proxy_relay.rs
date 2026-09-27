//! A loopback CONNECT bridge for upstream proxies requiring credentials.
//! Chromium does not support SOCKS5 username/password via --proxy-server.

use base64::{engine::general_purpose::STANDARD, Engine as _};
use serde_json::Value;
use std::{
    io::{self, Read, Write},
    net::{Shutdown, TcpListener, TcpStream, ToSocketAddrs},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    thread,
    time::Duration,
};

#[derive(Clone)]
struct Upstream {
    scheme: String,
    host: String,
    port: u16,
    username: String,
    password: String,
}

impl Upstream {
    fn parse(value: &Value) -> Result<Self, String> {
        let scheme = value["scheme"].as_str().ok_or("Некорректный прокси")?;
        if scheme != "http" && scheme != "socks5" {
            return Err("Некорректный прокси".into());
        }
        let host = value["host"].as_str().ok_or("Некорректный прокси")?;
        let port = value["port"].as_u64().ok_or("Некорректный прокси")?;
        let username = value["username"].as_str().ok_or("Нет логина прокси")?;
        let password = value["password"].as_str().ok_or("Нет пароля прокси")?;
        if host.is_empty()
            || port == 0
            || port > u16::MAX as u64
            || username.is_empty()
            || password.is_empty()
        {
            return Err("Некорректный прокси".into());
        }
        Ok(Self {
            scheme: scheme.into(),
            host: host.into(),
            port: port as u16,
            username: username.into(),
            password: password.into(),
        })
    }
}

pub fn start(value: &Value) -> Result<(u16, Arc<AtomicBool>), String> {
    let upstream = Upstream::parse(value)?;
    let listener =
        TcpListener::bind("127.0.0.1:0").map_err(|_| "Не удалось запустить локальный прокси")?;
    let port = listener
        .local_addr()
        .map_err(|_| "Нет порта локального прокси")?
        .port();
    listener
        .set_nonblocking(true)
        .map_err(|_| "Не удалось запустить локальный прокси")?;
    let stop = Arc::new(AtomicBool::new(false));
    let stopped = stop.clone();
    thread::spawn(move || {
        while !stopped.load(Ordering::Relaxed) {
            match listener.accept() {
                Ok((stream, peer)) if peer.ip().is_loopback() => {
                    if stream.set_nonblocking(false).is_err() {
                        continue;
                    }
                    let config = upstream.clone();
                    thread::spawn(move || {
                        let _ = serve(stream, &config);
                    });
                }
                Ok(_) => {}
                Err(err) if err.kind() == io::ErrorKind::WouldBlock => {
                    thread::sleep(Duration::from_millis(50))
                }
                Err(_) => break,
            }
        }
    });
    Ok((port, stop))
}

fn read_headers(stream: &mut TcpStream) -> io::Result<Vec<u8>> {
    let mut bytes = Vec::with_capacity(256);
    let mut byte = [0_u8; 1];
    while bytes.len() < 16_384 {
        stream.read_exact(&mut byte)?;
        bytes.push(byte[0]);
        if bytes.ends_with(b"\r\n\r\n") {
            return Ok(bytes);
        }
    }
    Err(io::Error::new(
        io::ErrorKind::InvalidData,
        "Header too large",
    ))
}

fn connect_upstream(config: &Upstream) -> io::Result<TcpStream> {
    let addresses = (config.host.as_str(), config.port).to_socket_addrs()?;
    for address in addresses {
        if let Ok(stream) = TcpStream::connect_timeout(&address, Duration::from_secs(10)) {
            stream.set_read_timeout(Some(Duration::from_secs(15)))?;
            stream.set_write_timeout(Some(Duration::from_secs(15)))?;
            return Ok(stream);
        }
    }
    Err(io::Error::new(
        io::ErrorKind::NotConnected,
        "Proxy unavailable",
    ))
}

fn serve(mut client: TcpStream, config: &Upstream) -> io::Result<()> {
    client.set_read_timeout(Some(Duration::from_secs(15)))?;
    client.set_write_timeout(Some(Duration::from_secs(15)))?;
    let request = read_headers(&mut client)?;
    let line = request
        .split(|byte| *byte == b'\n')
        .next()
        .unwrap_or_default();
    let line = std::str::from_utf8(line).map_err(|_| io::ErrorKind::InvalidData)?;
    let mut parts = line.split_ascii_whitespace();
    let (Some("CONNECT"), Some(target), Some("HTTP/1.1" | "HTTP/1.0")) =
        (parts.next(), parts.next(), parts.next())
    else {
        client.write_all(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")?;
        return Ok(());
    };
    let (host, port) = target.rsplit_once(':').ok_or(io::ErrorKind::InvalidData)?;
    let port: u16 = port.parse().map_err(|_| io::ErrorKind::InvalidData)?;
    if host.is_empty()
        || host.len() > 253
        || !host
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-'))
        || port != 443
    {
        client.write_all(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")?;
        return Ok(());
    }
    let mut remote = match connect_upstream(config) {
        Ok(remote) => remote,
        Err(_) => {
            client.write_all(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")?;
            return Ok(());
        }
    };
    let result = if config.scheme == "http" {
        http_connect(&mut remote, target, config)
    } else {
        socks5_connect(&mut remote, host, port, config)
    };
    if result.is_err() {
        client.write_all(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")?;
        return Ok(());
    }
    client.write_all(b"HTTP/1.1 200 Connection Established\r\n\r\n")?;
    client.set_read_timeout(None)?;
    remote.set_read_timeout(None)?;
    tunnel(client, remote)
}

fn http_connect(remote: &mut TcpStream, target: &str, config: &Upstream) -> io::Result<()> {
    let auth = STANDARD.encode(format!("{}:{}", config.username, config.password));
    write!(
        remote,
        "CONNECT {target} HTTP/1.1\r\nHost: {target}\r\nProxy-Authorization: Basic {auth}\r\n\r\n"
    )?;
    let response = read_headers(remote)?;
    if response.starts_with(b"HTTP/1.1 200 ") || response.starts_with(b"HTTP/1.0 200 ") {
        Ok(())
    } else {
        Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "Proxy rejected connection",
        ))
    }
}

fn socks5_connect(
    remote: &mut TcpStream,
    host: &str,
    port: u16,
    config: &Upstream,
) -> io::Result<()> {
    let (user, pass) = (config.username.as_bytes(), config.password.as_bytes());
    if user.len() > 255 || pass.len() > 255 || host.len() > 255 {
        return Err(io::ErrorKind::InvalidInput.into());
    }
    remote.write_all(&[5, 1, 2])?;
    let mut reply = [0_u8; 2];
    remote.read_exact(&mut reply)?;
    if reply != [5, 2] {
        return Err(io::ErrorKind::PermissionDenied.into());
    }
    let mut auth = Vec::with_capacity(3 + user.len() + pass.len());
    auth.extend_from_slice(&[1, user.len() as u8]);
    auth.extend_from_slice(user);
    auth.push(pass.len() as u8);
    auth.extend_from_slice(pass);
    remote.write_all(&auth)?;
    remote.read_exact(&mut reply)?;
    if reply != [1, 0] {
        return Err(io::ErrorKind::PermissionDenied.into());
    }
    let mut connect = vec![5, 1, 0, 3, host.len() as u8];
    connect.extend_from_slice(host.as_bytes());
    connect.extend_from_slice(&port.to_be_bytes());
    remote.write_all(&connect)?;
    let mut head = [0_u8; 4];
    remote.read_exact(&mut head)?;
    if head[0] != 5 || head[1] != 0 {
        return Err(io::ErrorKind::ConnectionRefused.into());
    }
    let address_len = match head[3] {
        1 => 4,
        3 => {
            let mut length = [0_u8; 1];
            remote.read_exact(&mut length)?;
            length[0] as usize
        }
        4 => 16,
        _ => return Err(io::ErrorKind::InvalidData.into()),
    };
    let mut tail = vec![0_u8; address_len + 2];
    remote.read_exact(&mut tail)?;
    Ok(())
}

fn tunnel(mut client: TcpStream, mut remote: TcpStream) -> io::Result<()> {
    let mut client_read = client.try_clone()?;
    let mut remote_write = remote.try_clone()?;
    let forward = thread::spawn(move || {
        let _ = io::copy(&mut client_read, &mut remote_write);
        let _ = remote_write.shutdown(Shutdown::Write);
    });
    let result = io::copy(&mut remote, &mut client);
    let _ = client.shutdown(Shutdown::Both);
    let _ = remote.shutdown(Shutdown::Both);
    let _ = forward.join();
    result.map(|_| ())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn bridge_roundtrip(scheme: &str) {
        let upstream = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = upstream.local_addr().unwrap().port();
        let kind = scheme.to_owned();
        let server = thread::spawn(move || {
            let (mut stream, _) = upstream.accept().unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            if kind == "http" {
                let headers = read_headers(&mut stream).unwrap();
                let text = String::from_utf8(headers).unwrap();
                assert!(text.starts_with("CONNECT www.instagram.com:443 HTTP/1.1\r\n"));
                assert!(text.contains("Proxy-Authorization: Basic dXNlcjpwYXNz\r\n"));
                stream
                    .write_all(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    .unwrap();
            } else {
                let mut hello = [0; 3];
                stream.read_exact(&mut hello).unwrap();
                assert_eq!(hello, [5, 1, 2]);
                stream.write_all(&[5, 2]).unwrap();
                let mut auth = [0; 11];
                stream.read_exact(&mut auth).unwrap();
                assert_eq!(&auth, b"\x01\x04user\x04pass");
                stream.write_all(&[1, 0]).unwrap();
                let mut connect = [0; 24];
                stream.read_exact(&mut connect).unwrap();
                assert_eq!(&connect, b"\x05\x01\x00\x03\x11www.instagram.com\x01\xbb");
                stream.write_all(&[5, 0, 0, 1, 127, 0, 0, 1, 0, 0]).unwrap();
            }
            let mut ping = [0; 4];
            stream.read_exact(&mut ping).unwrap();
            assert_eq!(&ping, b"ping");
            stream.write_all(b"pong").unwrap();
        });
        let config = json!({"scheme":scheme,"host":"127.0.0.1","port":port,"username":"user","password":"pass"});
        let (local_port, stop) = start(&config).unwrap();
        let mut client = TcpStream::connect(("127.0.0.1", local_port)).unwrap();
        client
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        thread::sleep(Duration::from_millis(100));
        client
            .write_all(
                b"CONNECT www.instagram.com:443 HTTP/1.1\r\nHost: www.instagram.com:443\r\n\r\n",
            )
            .unwrap();
        assert!(read_headers(&mut client)
            .unwrap()
            .starts_with(b"HTTP/1.1 200"));
        client.write_all(b"ping").unwrap();
        let mut pong = [0; 4];
        client.read_exact(&mut pong).unwrap();
        assert_eq!(&pong, b"pong");
        drop(client);
        stop.store(true, Ordering::Relaxed);
        server.join().unwrap();
    }

    #[test]
    fn http_basic_proxy_roundtrip() {
        bridge_roundtrip("http");
    }

    #[test]
    fn socks5_password_proxy_roundtrip() {
        bridge_roundtrip("socks5");
    }
}
