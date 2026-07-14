// v2 route extractor (Babel AST) — route -> resolved allowedRoles -> controller file.
// Resolves role constants by REQUIRING the constants module (accurate, not regex).
// Usage: node routes.js <serverDir>  ->  JSON [{method, path, roles:[...], public, controllerFile}]
const path = require("path"), fs = require("fs");
const serverDir = process.argv[2] || process.cwd();
const parser = require(path.join(serverDir, "node_modules", "@babel/parser"));
let traverse = require(path.join(serverDir, "node_modules", "@babel/traverse"));
traverse = traverse.default || traverse;

// resolve the constants module (role sets) at runtime
let CONST = {};
try { CONST = require(path.join(serverDir, "constants")); } catch (e) { try { CONST = require(path.join(serverDir, "constants", "index.js")); } catch (_) {} }

const ROUTER_FILES = [
  path.join(serverDir, "router", "index.js"),
  path.join(serverDir, "router", "users.js"),
];
const METHODS = new Set(["get", "post", "put", "patch", "delete"]);

const out = [];
for (const rf of ROUTER_FILES) {
  if (!fs.existsSync(rf)) continue;
  const code = fs.readFileSync(rf, "utf8");
  let ast; try { ast = parser.parse(code, { sourceType: "module", plugins: ["optionalChaining", "objectRestSpread"] }); } catch (e) { continue; }
  const imports = {}; // localName -> relative file
  traverse(ast, {
    // const X = require('...')  and  const { a } = require('...')
    VariableDeclarator(p) {
      const init = p.node.init;
      if (init && init.type === "CallExpression" && init.callee.name === "require" && init.arguments[0] && init.arguments[0].value) {
        const src = init.arguments[0].value;
        if (p.node.id.type === "Identifier") imports[p.node.id.name] = src;
        else if (p.node.id.type === "ObjectPattern") p.node.id.properties.forEach((pr) => pr.key && (imports[pr.value.name || pr.key.name] = src));
      }
    },
  });
  const resolveRoles = (node) => {
    if (!node) return { roles: [], public: false };
    if (node.type === "ArrayExpression") return { roles: node.elements.map(memberName).filter(Boolean), public: false };
    if (node.type === "Identifier") { const v = CONST[node.name]; return { roles: Array.isArray(v) ? v : [], public: false }; }
    return { roles: [], public: false };
  };
  function memberName(n) {
    if (!n) return null;
    if (n.type === "MemberExpression") { const v = CONST[n.object.name] && CONST[n.object.name][n.property.name]; return v || n.property.name; }
    if (n.type === "Identifier") return n.name;
    return null;
  }
  traverse(ast, {
    CallExpression(p) {
      const c = p.node.callee;
      if (!(c && c.type === "MemberExpression" && METHODS.has((c.property.name || "").toLowerCase()))) return;
      const args = p.node.arguments;
      if (!args.length || !(args[0].type === "StringLiteral" || args[0].type === "TemplateLiteral")) return;
      const pth = args[0].type === "StringLiteral" ? args[0].value : args[0].quasis.map((q) => q.value.cooked).join(":x");
      let roles = [], isPublic = true, controller = null;
      for (const a of args.slice(1)) {
        // authentication({ allowedRoles }) / authentication() / authentication({ allowPublic })
        if (a.type === "CallExpression" && a.callee.name === "authentication") {
          isPublic = false;
          const opt = a.arguments[0];
          if (opt && opt.type === "ObjectExpression") {
            for (const pr of opt.properties) {
              if (pr.key && pr.key.name === "allowedRoles") roles = resolveRoles(pr.value).roles;
              if (pr.key && pr.key.name === "allowPublic" && pr.value.value === true) isPublic = true;
            }
          }
        } else if (a.type === "Identifier") controller = imports[a.name] || a.name; // last identifier = controller
      }
      out.push({ method: c.property.name.toUpperCase(), path: pth, roles, public: isPublic, controllerFile: controller });
    },
  });
}
process.stdout.write(JSON.stringify({ routes: out, role_consts: Object.keys(CONST).filter((k) => Array.isArray(CONST[k])) }));
