// App-wide constants

export const APP_NAME = "Skinny Dad | Dev & Gym";
export const ACCENT_COLOR = "#39ff14";
export const ACCENT_COLOR_DIM = "#2bcc10";

export const LABELS = {
  greeting: {
    morning: "Chào buổi sáng",
    afternoon: "Chào buổi chiều",
    evening: "Chào buổi tối",
  },
  stats: {
    scripts: "Kịch bản",
    published: "Đã đăng",
    upcoming: "Sắp tới",
    scriptsProgress: "đã quay",
  },
  dashboard: {
    upcomingPosts: "Bài sắp đăng",
    quickActions: "Hành động nhanh",
    recentActivity: "Hoạt động gần đây",
    emptyUpcoming: "Chưa có bài nào được lên lịch",
    emptyActivity: "Chưa có hoạt động nào",
  },
  actions: {
    createContent: "Tạo nội dung mới",
    viewSchedule: "Xem lịch đăng",
    manageDrafts: "Quản lý bản nháp",
  },
  time: {
    inHours: "trong {n} giờ",
    inMinutes: "trong {n} phút",
    tomorrow: "ngày mai lúc {time}",
    today: "hôm nay lúc {time}",
  },
} as const;

export const VIETNAMESE_DAYS = [
  "Chủ nhật",
  "Thứ hai",
  "Thứ ba",
  "Thứ tư",
  "Thứ năm",
  "Thứ sáu",
  "Thứ bảy",
] as const;

export const VIETNAMESE_MONTHS = [
  "Tháng 1",
  "Tháng 2",
  "Tháng 3",
  "Tháng 4",
  "Tháng 5",
  "Tháng 6",
  "Tháng 7",
  "Tháng 8",
  "Tháng 9",
  "Tháng 10",
  "Tháng 11",
  "Tháng 12",
] as const;
