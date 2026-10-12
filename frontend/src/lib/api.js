import axios from "axios";
import { formatDate } from "@/lib/utils";

export const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

axios.defaults.withCredentials = true;

export function formatApiErrorDetail(detail) {
  if (detail == null) return "Κάτι πήγε στραβά. Δοκιμάστε ξανά.";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail.map((e) => (e && typeof e.msg === "string" ? e.msg : JSON.stringify(e))).filter(Boolean).join(" ");
  if (detail && typeof detail.msg === "string") return detail.msg;
  return String(detail);
}

export function errMsg(e) {
  return formatApiErrorDetail(e.response?.data?.detail) || e.message;
}

export const money = (v) =>
  new Intl.NumberFormat("el-GR", { style: "currency", currency: "EUR" }).format(v || 0);

// Ημερομηνία ή σφραγίδα του server ως ΗΗ/ΜΜ/ΕΕΕΕ ώρας Ελλάδας, ανεξάρτητα από τη ζώνη της συσκευής.
export const dateFmt = formatDate;

export default axios;
