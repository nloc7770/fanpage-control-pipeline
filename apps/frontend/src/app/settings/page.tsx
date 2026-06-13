"use client";

import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Settings, Zap, Target } from "lucide-react";

const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080").replace(/\/+$/, "");

async function checkToken(): Promise<{ valid: boolean; page_name: string }> {
  const res = await fetch(`${API_BASE}/healthz`, { cache: "no-store" });
  if (!res.ok) throw new Error("API unreachable");
  return { valid: true, page_name: "Fitviet / Skinny Dad" };
}

export default function SettingsPage() {
  const [dailyTarget, setDailyTarget] = useState(3);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<"success" | "error" | null>(null);

  const { data: tokenStatus, isError } = useQuery({
    queryKey: ["token-check"],
    queryFn: checkToken,
    refetchInterval: 60_000,
    retry: 1,
  });

  useEffect(() => {
    try {
      const stored = localStorage.getItem("factory.dailyTarget");
      if (stored) setDailyTarget(Number(stored));
    } catch {
      // ignore
    }
  }, []);

  function handleTargetChange(value: number) {
    const clamped = Math.max(1, Math.min(20, value));
    setDailyTarget(clamped);
    try {
      localStorage.setItem("factory.dailyTarget", String(clamped));
    } catch {
      // ignore
    }
  }

  async function handleTestToken() {
    setTesting(true);
    setTestResult(null);
    try {
      await fetch(`${API_BASE}/healthz`, { cache: "no-store" });
      setTestResult("success");
    } catch {
      setTestResult("error");
    } finally {
      setTesting(false);
    }
  }

  const isConnected = !!tokenStatus && !isError;

  return (
    <div className="mx-auto max-w-lg space-y-6">
      <div className="flex items-center gap-3">
        <Settings className="h-6 w-6 text-neon" />
        <h1 className="text-2xl font-bold tracking-tight">Cài đặt</h1>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Zap className="h-4 w-4" />
            Kết nối Fanpage
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium">Fitviet / Skinny Dad</p>
              <p className="text-xs text-muted-foreground">Facebook Page</p>
            </div>
            <Badge
              variant={isConnected ? "default" : "destructive"}
              className={isConnected ? "bg-neon/20 text-neon border-neon/30" : ""}
            >
              {isConnected ? "Đã kết nối" : "Mất kết nối"}
            </Badge>
          </div>

          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={handleTestToken}
              disabled={testing}
            >
              {testing ? "Đang kiểm tra..." : "Test Token"}
            </Button>
            {testResult === "success" && (
              <span className="text-xs text-neon">Token hoạt động tốt</span>
            )}
            {testResult === "error" && (
              <span className="text-xs text-rose-400">Không thể kết nối API</span>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Target className="h-4 w-4" />
            Mục tiêu hàng ngày
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center gap-3">
            <Input
              type="number"
              min={1}
              max={20}
              value={dailyTarget}
              onChange={(e) => handleTargetChange(Number(e.target.value))}
              className="w-20"
              aria-label="Số bài đăng mỗi ngày"
            />
            <span className="text-sm text-muted-foreground">bài đăng / ngày</span>
          </div>
          <p className="text-xs text-muted-foreground">
            Số lượng bài viết mục tiêu đăng mỗi ngày trên fanpage.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
