/** Minimal JSON Schema -> TypeScript type converter for MCP tool schemas. */

type Schema = {
  type?: string | string[];
  properties?: Record<string, Schema>;
  required?: string[];
  items?: Schema | Schema[];
  enum?: unknown[];
  const?: unknown;
  anyOf?: Schema[];
  oneOf?: Schema[];
  allOf?: Schema[];
  additionalProperties?: boolean | Schema;
  description?: string;
  $ref?: string;
};

const IDENT = /^[A-Za-z_$][A-Za-z0-9_$]*$/;

export function schemaToTs(schema: Schema | undefined, indent = ""): string {
  if (!schema || typeof schema !== "object") return "any";
  if (schema.const !== undefined) return JSON.stringify(schema.const);
  if (schema.enum) return schema.enum.map((v) => JSON.stringify(v)).join(" | ") || "never";
  if (schema.anyOf || schema.oneOf) return (schema.anyOf ?? schema.oneOf)!.map((s) => schemaToTs(s, indent)).join(" | ");
  if (schema.allOf) return schema.allOf.map((s) => schemaToTs(s, indent)).join(" & ");
  if (schema.$ref) return "any";

  const types = Array.isArray(schema.type) ? schema.type : schema.type ? [schema.type] : [];
  if (types.length > 1) return types.map((t) => schemaToTs({ ...schema, type: t }, indent)).join(" | ");

  switch (types[0]) {
    case "string":
      return "string";
    case "number":
    case "integer":
      return "number";
    case "boolean":
      return "boolean";
    case "null":
      return "null";
    case "array": {
      if (Array.isArray(schema.items)) return `[${schema.items.map((s) => schemaToTs(s, indent)).join(", ")}]`;
      const inner = schemaToTs(schema.items, indent);
      return /[|&]/.test(inner) ? `Array<${inner}>` : `${inner}[]`;
    }
    case "object":
    case undefined:
      if (!schema.properties) {
        if (types[0] === undefined) return "any";
        const ap = schema.additionalProperties;
        return `Record<string, ${typeof ap === "object" ? schemaToTs(ap, indent) : "any"}>`;
      }
      return objectBody(schema, indent);
    default:
      return "any";
  }
}

function objectBody(schema: Schema, indent: string): string {
  const req = new Set(schema.required ?? []);
  const inner = indent + "  ";
  const lines = Object.entries(schema.properties ?? {}).map(([key, prop]) => {
    const name = IDENT.test(key) ? key : JSON.stringify(key);
    const doc = prop.description ? `${inner}/** ${oneLine(prop.description)} */\n` : "";
    return `${doc}${inner}${name}${req.has(key) ? "" : "?"}: ${schemaToTs(prop, inner)};`;
  });
  return `{\n${lines.join("\n")}\n${indent}}`;
}

export function oneLine(s: string): string {
  return s.replace(/\*\//g, "* /").replace(/\s+/g, " ").trim();
}
