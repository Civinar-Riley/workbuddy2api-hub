// SPDX-License-Identifier: MIT

package main

import (
	"bufio"
	"crypto/tls"
	"crypto/subtle"
	"encoding/base64"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	utls "github.com/refraction-networking/utls"
)

var officialCipherSuites = []uint16{
	tls.TLS_AES_128_GCM_SHA256,
	tls.TLS_AES_256_GCM_SHA384,
	tls.TLS_CHACHA20_POLY1305_SHA256,
	tls.TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256,
	tls.TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256,
	tls.TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384,
	tls.TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384,
	tls.TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA256,
	tls.TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305_SHA256,
	tls.TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256,
	tls.TLS_ECDHE_ECDSA_WITH_AES_128_CBC_SHA,
	tls.TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA,
	tls.TLS_ECDHE_ECDSA_WITH_AES_256_CBC_SHA,
	tls.TLS_ECDHE_RSA_WITH_AES_256_CBC_SHA,
	tls.TLS_RSA_WITH_AES_128_GCM_SHA256,
	tls.TLS_RSA_WITH_AES_256_GCM_SHA384,
	tls.TLS_RSA_WITH_AES_128_CBC_SHA,
	tls.TLS_RSA_WITH_AES_256_CBC_SHA,
}

var officialSigAlgs = []utls.SignatureScheme{
	0x0403,
	0x0804,
	0x0401,
	0x0503,
	0x0805,
	0x0501,
	0x0806,
	0x0601,
	0x0201,
}

func officialHelloSpec() *utls.ClientHelloSpec {
	return &utls.ClientHelloSpec{
		TLSVersMin:         tls.VersionTLS12,
		TLSVersMax:         tls.VersionTLS13,
		CipherSuites:       officialCipherSuites,
		CompressionMethods: []byte{0},
		Extensions: []utls.TLSExtension{
			&utls.SNIExtension{},
			&utls.UtlsExtendedMasterSecretExtension{},
			&utls.RenegotiationInfoExtension{
				Renegotiation: utls.RenegotiateOnceAsClient,
			},
			&utls.SupportedCurvesExtension{Curves: []utls.CurveID{
				utls.X25519,
				utls.CurveP256,
				utls.CurveP384,
			}},
			&utls.SupportedPointsExtension{SupportedPoints: []byte{0}},
			&utls.SessionTicketExtension{},
			&utls.SignatureAlgorithmsExtension{
				SupportedSignatureAlgorithms: officialSigAlgs,
			},
			&utls.KeyShareExtension{KeyShares: []utls.KeyShare{
				{Group: utls.X25519},
			}},
			&utls.PSKKeyExchangeModesExtension{
				Modes: []uint8{utls.PskModeDHE},
			},
			&utls.SupportedVersionsExtension{Versions: []uint16{
				tls.VersionTLS13,
				tls.VersionTLS12,
			}},
		},
	}
}

func main() {
	listen := flag.String("listen", "127.0.0.1:0", "loopback address to listen on")
	parentWatch := flag.Bool("parent-watch", false, "exit when stdin reaches EOF")
	flag.Parse()

	reader := bufio.NewReader(os.Stdin)
	secret, err := reader.ReadString('\n')
	if err != nil || len(strings.TrimSpace(secret)) < 32 {
		log.Fatal("bridge authentication secret required on stdin")
	}
	secret = strings.TrimSpace(secret)
	if *parentWatch {
		go func() { _, _ = io.Copy(io.Discard, reader); os.Exit(0) }()
	}
	host, _, err := net.SplitHostPort(*listen)
	if err != nil || (host != "127.0.0.1" && host != "::1" && host != "localhost") {
		log.Fatal("bridge must listen on loopback")
	}

	listener, err := net.Listen("tcp", *listen)
	if err != nil {
		log.Fatal(err)
	}
	defer listener.Close()

	fmt.Fprintf(os.Stdout, "LISTEN %s\n", listener.Addr().String())
	_ = os.Stdout.Sync()

	for {
		conn, err := listener.Accept()
		if err != nil {
			if netErr, ok := err.(net.Error); ok && netErr.Temporary() {
				time.Sleep(50 * time.Millisecond)
				continue
			}
			return
		}
		go serveClient(conn, secret)
	}
}

func serveClient(client net.Conn, secret string) {
	defer client.Close()
	_ = client.SetDeadline(time.Now().Add(15 * time.Second))

	reader := bufio.NewReader(client)
	req, err := http.ReadRequest(reader)
	if err != nil {
		writeHTTPError(client, http.StatusBadRequest, "Bad Request")
		return
	}
	if req.Method != http.MethodConnect {
		writeHTTPError(client, http.StatusMethodNotAllowed, "CONNECT Required")
		return
	}
	provided := req.Header.Get("X-WB-Bridge-Auth")
	if subtle.ConstantTimeCompare([]byte(provided), []byte(secret)) != 1 {
		writeHTTPError(client, http.StatusForbidden, "Forbidden")
		return
	}

	target := strings.TrimSpace(req.Host)
	if target == "" {
		target = strings.TrimSpace(req.RequestURI)
	}
	host, err := connectTargetHost(target)
	if err != nil {
		writeHTTPError(client, http.StatusBadRequest, "Bad CONNECT Target")
		return
	}

	var raw net.Conn
	proxyURL := strings.TrimSpace(req.Header.Get("X-WB-Upstream-Proxy"))
	if proxyURL != "" {
		raw, err = dialProxyTunnel(proxyURL, target)
	} else {
		raw, err = net.DialTimeout("tcp", target, 10*time.Second)
	}
	if err != nil {
		writeHTTPError(client, http.StatusBadGateway, "Upstream Connect Failed")
		return
	}
	defer raw.Close()

	_ = raw.SetDeadline(time.Now().Add(15 * time.Second))
	tlsConn := utls.UClient(raw, &utls.Config{
		ServerName: host,
		MinVersion: tls.VersionTLS12,
	}, utls.HelloCustom)
	if err := tlsConn.ApplyPreset(officialHelloSpec()); err != nil {
		writeHTTPError(client, http.StatusBadGateway, "TLS Fingerprint Failed")
		return
	}
	if err := tlsConn.Handshake(); err != nil {
		writeHTTPError(client, http.StatusBadGateway, "TLS Handshake Failed")
		return
	}
	_ = raw.SetDeadline(time.Time{})
	_ = client.SetDeadline(time.Time{})

	if _, err := io.WriteString(client,
		"HTTP/1.1 200 Connection Established\r\n\r\n"); err != nil {
		return
	}
	proxyBidirectional(client, reader, tlsConn)
}

func connectTargetHost(target string) (string, error) {
	host, port, err := net.SplitHostPort(target)
	if err != nil || strings.TrimSpace(host) == "" {
		return "", fmt.Errorf("invalid CONNECT target")
	}
	number, err := strconv.Atoi(port)
	if err != nil || number < 1 || number > 65535 {
		return "", fmt.Errorf("invalid CONNECT port")
	}
	return host, nil
}

func dialProxyTunnel(rawProxyURL, target string) (net.Conn, error) {
	proxyURL, err := url.Parse(rawProxyURL)
	if err != nil || !strings.EqualFold(proxyURL.Scheme, "http") ||
		proxyURL.Hostname() == "" {
		return nil, fmt.Errorf("invalid HTTP proxy URL")
	}
	proxyPort := proxyURL.Port()
	if proxyPort == "" {
		proxyPort = "80"
	}
	proxyAddr := net.JoinHostPort(proxyURL.Hostname(), proxyPort)

	conn, err := net.DialTimeout("tcp", proxyAddr, 10*time.Second)
	if err != nil {
		return nil, err
	}
	_ = conn.SetDeadline(time.Now().Add(10 * time.Second))

	req := fmt.Sprintf("CONNECT %s HTTP/1.1\r\nHost: %s\r\n", target, target)
	if proxyURL.User != nil {
		password, _ := proxyURL.User.Password()
		credentials := proxyURL.User.Username() + ":" + password
		token := base64.StdEncoding.EncodeToString([]byte(credentials))
		req += fmt.Sprintf("Proxy-Authorization: Basic %s\r\n", token)
	}
	req += "\r\n"
	if _, err := io.WriteString(conn, req); err != nil {
		conn.Close()
		return nil, err
	}

	reader := bufio.NewReader(conn)
	resp, err := http.ReadResponse(reader, &http.Request{Method: http.MethodConnect})
	if err != nil {
		conn.Close()
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		resp.Body.Close()
		conn.Close()
		return nil, fmt.Errorf("proxy CONNECT returned %d", resp.StatusCode)
	}
	return conn, nil
}

func proxyBidirectional(client net.Conn, clientReader io.Reader, upstream net.Conn) {
	done := make(chan struct{}, 2)
	go func() {
		_, _ = io.Copy(upstream, clientReader)
		done <- struct{}{}
	}()
	go func() {
		_, _ = io.Copy(client, upstream)
		done <- struct{}{}
	}()
	<-done
	client.Close()
	upstream.Close()
	<-done
}

func writeHTTPError(conn net.Conn, status int, reason string) {
	body := reason + "\n"
	fmt.Fprintf(conn,
		"HTTP/1.1 %d %s\r\nContent-Type: text/plain\r\nContent-Length: %d\r\n"+
			"Connection: close\r\n\r\n%s",
		status, http.StatusText(status), len(body), body)
}
