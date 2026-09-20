// 日期格式化接口；共用客户端状态，保持既有请求契约。

// 本模块负责 dates 接口，复用共享客户端和运行时状态。

// Shared date formatter for daily activity endpoints
export const formatDate = (date: Date) => {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
};
