import fs from "node:fs";
import path from "node:path";
import { SERVERS_DIR, SKILLS_DIR } from "./config.ts";

export interface SkillInfo {
  name: string;
  description: string;
  dir: string;
}

/** Reads name/description from each skills/<name>/SKILL.md frontmatter. */
export function listSkills(dir = SKILLS_DIR): SkillInfo[] {
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir, { withFileTypes: true })
    .filter((d) => d.isDirectory() && fs.existsSync(path.join(dir, d.name, "SKILL.md")))
    .map((d) => {
      const md = fs.readFileSync(path.join(dir, d.name, "SKILL.md"), "utf8");
      const fm = /^---\n([\s\S]*?)\n---/.exec(md)?.[1] ?? "";
      const field = (k: string) => new RegExp(`^${k}:\\s*(.+)$`, "m").exec(fm)?.[1].trim();
      return { name: field("name") ?? d.name, description: field("description") ?? "", dir: d.name };
    })
    .sort((a, b) => a.dir.localeCompare(b.dir));
}

export function listServers(dir = SERVERS_DIR): string[] {
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir, { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => d.name)
    .sort();
}
