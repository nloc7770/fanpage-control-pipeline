"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { useCreateDraftFromScript } from "@/hooks/use-scripts";
import type { ScriptDTO } from "@/hooks/use-scripts";

interface CreateFromScriptDialogProps {
  script: ScriptDTO;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CreateFromScriptDialog({ script, open, onOpenChange }: CreateFromScriptDialogProps) {
  const router = useRouter();
  const { toast } = useToast();
  const mutation = useCreateDraftFromScript();

  const [title, setTitle] = useState(script.title);
  const [caption, setCaption] = useState(script.caption);
  const [hashtags, setHashtags] = useState(script.hashtags.join(" "));

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const parsedHashtags = hashtags
      .split(/[\s,]+/)
      .map((h) => h.trim())
      .filter(Boolean);

    mutation.mutate(
      {
        slug: script.slug,
        body: { title, caption, hashtags: parsedHashtags },
      },
      {
        onSuccess: () => {
          toast({ title: "Tạo Reel thành công", description: "Đã tạo draft từ kịch bản", variant: "success" });
          onOpenChange(false);
          router.push("/content");
        },
        onError: (err) => {
          toast({ title: "Lỗi", description: (err as Error).message, variant: "destructive" });
        },
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Tạo Reel từ kịch bản</DialogTitle>
          <DialogDescription>
            Thông tin sẽ được sử dụng để tạo draft mới. Bạn có thể chỉnh sửa trước khi gửi.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <label htmlFor="draft-title" className="text-sm font-medium text-foreground">
              Tiêu đề
            </label>
            <input
              id="draft-title"
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              required
            />
          </div>

          <div className="space-y-2">
            <label htmlFor="draft-caption" className="text-sm font-medium text-foreground">
              Caption
            </label>
            <textarea
              id="draft-caption"
              value={caption}
              onChange={(e) => setCaption(e.target.value)}
              rows={3}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring resize-none"
            />
          </div>

          <div className="space-y-2">
            <label htmlFor="draft-hashtags" className="text-sm font-medium text-foreground">
              Hashtags
            </label>
            <input
              id="draft-hashtags"
              type="text"
              value={hashtags}
              onChange={(e) => setHashtags(e.target.value)}
              placeholder="#gym #transformation #day1"
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
            <p className="text-xs text-muted-foreground">Phân cách bằng dấu cách hoặc dấu phẩy</p>
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => onOpenChange(false)}
              disabled={mutation.isPending}
            >
              Huỷ
            </Button>
            <Button
              type="submit"
              disabled={mutation.isPending || !title.trim()}
              className="bg-neon text-black hover:bg-neon/90 font-semibold"
            >
              {mutation.isPending ? "Đang tạo..." : "Tạo Reel"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default CreateFromScriptDialog;
