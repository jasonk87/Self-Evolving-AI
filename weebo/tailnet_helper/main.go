// Weebo's private HTTPS address on the owner's tailnet. This userspace Tailscale node
// has its own hostname/state and never changes the PC's existing VPN adapter.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"tailscale.com/tsnet"
)

type endpointStatus struct {
	State   string `json:"state"`
	AuthURL string `json:"authUrl,omitempty"`
	Origin  string `json:"origin,omitempty"`
	Owner   string `json:"owner,omitempty"`
	Error   string `json:"error,omitempty"`
	PID     int    `json:"pid"`
}
type identify func(*http.Request) (string, error)

func proxyHandler(target *url.URL, host string, who identify) http.Handler {
	proxy := httputil.NewSingleHostReverseProxy(target)
	proxy.Transport = &http.Transport{DialContext: (&net.Dialer{Timeout: 5 * time.Second}).DialContext, ResponseHeaderTimeout: 30 * time.Second}
	proxy.FlushInterval = -1 // Stream replies and events as they happen.
	director := proxy.Director
	proxy.Director = func(r *http.Request) {
		originalHost := r.Host
		director(r)
		r.Host = originalHost
		for key := range r.Header {
			lower := strings.ToLower(key)
			if strings.HasPrefix(lower, "tailscale-") || strings.HasPrefix(lower, "x-forwarded-") || lower == "forwarded" || lower == "x-real-ip" {
				r.Header.Del(key)
			}
		}
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Host != host && r.Host != host+":443" {
			http.Error(w, "Invalid Weebo hostname", http.StatusForbidden)
			return
		}
		r.Host = host
		login, err := who(r)
		if err != nil {
			http.Error(w, "Cannot verify the Tailscale connection", http.StatusUnauthorized)
			return
		}
		// Identity is installed after the director strips every client-supplied copy.
		wrapped := *proxy
		wrapped.Director = func(out *http.Request) {
			proxy.Director(out)
			if login != "" {
				out.Header.Set("Tailscale-User-Login", login)
			}
		}
		wrapped.ServeHTTP(w, r)
	})
}

func main() {
	stateDir := flag.String("state", "", "Weebo-owned state directory")
	hostname := flag.String("hostname", "weebo", "Dedicated Tailscale machine name")
	targetText := flag.String("target", "http://127.0.0.1:5050", "Loopback Weebo URL")
	flag.Parse()
	target, err := url.Parse(*targetText)
	if err != nil || target.Scheme != "http" || target.Hostname() != "127.0.0.1" || target.User != nil || target.Path != "" || target.RawQuery != "" || target.Fragment != "" {
		log.Fatal("Target must be an HTTP loopback origin")
	}
	if !filepath.IsAbs(*stateDir) {
		log.Fatal("State directory must be absolute")
	}
	if err = os.MkdirAll(*stateDir, 0700); err != nil {
		log.Fatal(err)
	}
	os.Setenv("TS_NO_LOGS_NO_SUPPORT", "true")
	// Clear inherited enrollment secrets. Use this endpoint's saved identity or
	// the explicit one-time Tailscale device connection shown to its owner.
	for _, key := range []string{"TS_AUTHKEY", "TS_AUTH_KEY", "TS_CLIENT_SECRET", "TS_CLIENT_ID", "TS_ID_TOKEN", "TS_AUDIENCE", "TSNET_FORCE_LOGIN"} {
		os.Unsetenv(key)
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt)
	defer cancel()
	srv := &tsnet.Server{Dir: filepath.Join(*stateDir, "node"), Hostname: *hostname, UserLogf: log.Printf}
	defer srv.Close()
	if err = os.MkdirAll(srv.Dir, 0700); err != nil {
		log.Fatal(err)
	}
	statusPath := filepath.Join(*stateDir, "status.json")
	var mu sync.Mutex
	current := endpointStatus{State: "starting", PID: os.Getpid()}
	save := func(s endpointStatus) {
		mu.Lock()
		defer mu.Unlock()
		if current.State == "ready" && s.State != "ready" && s.State != "error" {
			return
		}
		s.PID = os.Getpid()
		current = s
		b, _ := json.MarshalIndent(s, "", "  ")
		temp := statusPath + ".tmp"
		if os.WriteFile(temp, b, 0600) == nil {
			if e := os.Rename(temp, statusPath); e != nil {
				log.Printf("Status checkpoint: %v", e)
			}
		}
	}
	save(current)
	fail := func(e error) { save(endpointStatus{State: "error", Error: e.Error()}); log.Print(e) }
	lc, err := srv.LocalClient()
	if err != nil {
		fail(err)
		return
	}
	go func() {
		ticker := time.NewTicker(2 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				mu.Lock()
				ready := current.State == "ready"
				mu.Unlock()
				if !ready {
					st, e := lc.Status(ctx)
					if e == nil {
						save(endpointStatus{State: st.BackendState, AuthURL: st.AuthURL})
					}
				}
				if _, e := os.Stat(filepath.Join(*stateDir, "stop")); e == nil {
					cancel()
					return
				}
			}
		}
	}()
	st, err := srv.Up(ctx)
	if err != nil {
		fail(err)
		return
	}
	if st.Self == nil {
		fail(errors.New("Tailscale did not provide this endpoint's identity"))
		return
	}
	host := strings.TrimSuffix(st.Self.DNSName, ".")
	if host == "" {
		fail(errors.New("Tailscale did not assign a DNS name"))
		return
	}
	owner := st.User[st.Self.UserID].LoginName
	ln, err := srv.ListenTLS("tcp", ":443")
	if err != nil {
		fail(err)
		return
	}
	handler := proxyHandler(target, host, func(r *http.Request) (string, error) {
		who, e := lc.WhoIs(r.Context(), r.RemoteAddr)
		if e != nil {
			return "", e
		}
		if who.UserProfile == nil || who.Node == nil || who.Node.IsTagged() {
			return "", nil
		}
		return who.UserProfile.LoginName, nil
	})
	web := &http.Server{Handler: handler, ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 32768}
	save(endpointStatus{State: "ready", Origin: "https://" + host, Owner: owner})
	fmt.Println("Weebo private address: https://" + host)
	go func() {
		<-ctx.Done()
		shutdown, c := context.WithTimeout(context.Background(), 5*time.Second)
		defer c()
		web.Shutdown(shutdown)
	}()
	if err = web.Serve(ln); err != nil && !errors.Is(err, http.ErrServerClosed) {
		fail(err)
	}
}
