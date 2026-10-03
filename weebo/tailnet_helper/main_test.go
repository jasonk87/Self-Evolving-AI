package main

import (
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
)

func TestProxyVerifiedIdentityAndRouting(t *testing.T) {
	called := false
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called = true
		if r.Host != "weebo.example.ts.net" {
			t.Errorf("wrong host: %s", r.Host)
		}
		if r.Header.Get("Tailscale-User-Login") != "owner@example.test" {
			t.Error("identity was not replaced")
		}
		for _, h := range []string{"Tailscale-User-Name", "Tailscale-App-Capabilities", "Forwarded", "X-Real-Ip", "X-Forwarded-Host", "X-Forwarded-Proto"} {
			if r.Header.Get(h) != "" {
				t.Errorf("forged header survived: %s", h)
			}
		}
		if r.Method != "POST" || r.URL.String() != "/api/stop?fixture=1" || r.Header.Get("X-Weebo-Token") != "csrf" {
			t.Error("request was changed")
		}
		b, _ := io.ReadAll(r.Body)
		if string(b) != "{}" {
			t.Error("body changed")
		}
		w.Header().Set("Set-Cookie", "fixture=only")
		w.WriteHeader(202)
		w.Write([]byte("saved"))
	}))
	defer backend.Close()
	target, _ := url.Parse(backend.URL)
	handler := proxyHandler(target, "weebo.example.ts.net", func(*http.Request) (string, error) { return "owner@example.test", nil })
	req := httptest.NewRequest("POST", "https://weebo.example.ts.net:443/api/stop?fixture=1", strings.NewReader("{}"))
	for _, h := range []string{"Tailscale-User-Login", "Tailscale-User-Name", "Tailscale-App-Capabilities", "Forwarded", "X-Real-Ip", "X-Forwarded-Host", "X-Forwarded-Proto"} {
		req.Header.Set(h, "forged")
	}
	req.Header.Set("X-Weebo-Token", "csrf")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, req)
	if !called || response.Code != 202 || response.Body.String() != "saved" || response.Header().Get("Set-Cookie") != "fixture=only" {
		t.Error("backend response was not forwarded")
	}
}
func TestProxyFailsClosedWithoutVerifiedConnection(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { t.Error("request reached backend") }))
	defer backend.Close()
	target, _ := url.Parse(backend.URL)
	handler := proxyHandler(target, "weebo.example.ts.net", func(*http.Request) (string, error) { return "", errors.New("unknown peer") })
	for _, check := range []struct {
		host string
		code int
	}{{"orion.example.ts.net", 403}, {"weebo.example.ts.net", 401}} {
		req := httptest.NewRequest("GET", "https://"+check.host+"/", nil)
		req.Header.Set("Tailscale-User-Login", "forged")
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, req)
		if response.Code != check.code {
			t.Errorf("%s: %d", check.host, response.Code)
		}
	}
}
func TestProxyNoIdentityCannotForgeLogin(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Tailscale-User-Login") != "" {
			t.Error("unverified identity survived")
		}
		w.WriteHeader(401)
	}))
	defer backend.Close()
	target, _ := url.Parse(backend.URL)
	handler := proxyHandler(target, "weebo.example.ts.net", func(*http.Request) (string, error) { return "", nil })
	req := httptest.NewRequest("GET", "https://weebo.example.ts.net/", nil)
	req.Header.Set("Tailscale-User-Login", "forged")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, req)
	if response.Code != 401 {
		t.Error("backend authentication changed")
	}
}
