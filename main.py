import sys
import uuid
import yaml
import json
import asyncio
import requests
 
CONTROLLER_IP = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
REST_PORT = 8080
PRIORITY = "32768"
 
SFP_CANDIDATES = ["/wm/staticflowpusher", "/wm/staticentrypusher"]
SFP_BASE = None
 
 

class Student:
    _counter = 20260001
 
    def __init__(self, name, mac, code=None):
        self.name = name.strip()
        self.mac = mac.strip().lower()
        if code is None:
            code = str(Student._counter)
            Student._counter += 1
        self.code = str(code)
 
    def to_dict(self):
        return {"name": self.name, "mac": self.mac, "code": self.code}
 
    def __str__(self):
        return f"[{self.code}] {self.name:<25} MAC: {self.mac}"
 
 
class Service:
    def __init__(self, name, protocol, port):
        self.name = name
        self.protocol = protocol.upper()
        self.port = int(port)
 
    def to_dict(self):
        return {"name": self.name, "protocol": self.protocol, "port": self.port}
 
    def __str__(self):
        return f"{self.name:<6} {self.protocol}/{self.port}"
 
 
class Server:
    def __init__(self, name, ip, services):
        self.name = name
        self.ip = ip
        self.services = [Service(s["name"], s["protocol"], s["port"]) for s in services]
 
    def get_service(self, name):
        for s in self.services:
            if s.name == name:
                return s
        return None
 
    def to_dict(self):
        return {"name": self.name, "ip": self.ip,
                "services": [s.to_dict() for s in self.services]}
 
    def __str__(self):
        return f"{self.name:<10} IP: {self.ip}"
 
 
class Course:
    def __init__(self, name, state, students, servers, code=None):
        self.name = name
        self.code = code
        self.state = state.upper()
        self.students = list(students or [])          # nombres de alumnos
        # servers: [{"name": "Server 3", "allowed_services": ["ssh", "web"]}]
        self.servers = [{"name": s["name"], "allowed_services": list(s.get("allowed_services", []))}
                        for s in (servers or [])]
 
    def add_student(self, name):
        if name not in self.students:
            self.students.append(name)
            return True
        return False
 
    def remove_student(self, name):
        if name in self.students:
            self.students.remove(name)
            return True
        return False
 
    def allows(self, server_name, service_name):
        if self.state != "ACTIVE":
            return False
        for s in self.servers:
            if s["name"] == server_name and service_name in s["allowed_services"]:
                return True
        return False
 
    def to_dict(self):
        return {"name": self.name, "code": self.code, "state": self.state,
                "students": self.students, "servers": self.servers}
 
    def __str__(self):
        return f"{self.code:<8} {self.name:<32} {self.state}"
 
 
class Connection:
    def __init__(self, student, server, services, path, flows):
        self.handler = uuid.uuid4().hex[:8]
        self.student = student      # Student
        self.server = server        # Server
        self.services = services    # [Service]
        self.path = path            # [(dpid, in_port, out_port), ...]
        self.flows = flows          # [(dpid, flow_name), ...]
 
    def __str__(self):
        svcs = ", ".join(s.name for s in self.services)
        return f"[{self.handler}] {self.student.name} -> {self.server.name} ({self.server.ip}) [{svcs}]"
 
 

students, servers, courses, connections = [], [], [], []
 
 
def load_data(filename):
    with open(filename, "r") as file:
        data = yaml.safe_load(file)
    students = [Student(x["name"], x["mac"], x.get("code")) for x in data["students"]]
    servers = [Server(y["name"], y["ip"], y["services"]) for y in data["servers"]]
    courses = [Course(z["name"], z["state"], z["students"], z["servers"], z.get("code"))
               for z in data["courses"]]
    return students, servers, courses
 
 
def save_data(filename):
    data = {"students": [s.to_dict() for s in students],
            "courses": [c.to_dict() for c in courses],
            "servers": [s.to_dict() for s in servers]}
    with open(filename, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
 
 
def find_student(name):
    for s in students:
        if s.name.lower() == name.strip().lower() or s.code == name.strip():
            return s
    return None
 
 
def find_course(code):
    for c in courses:
        if (c.code or "").lower() == code.strip().lower() or c.name.lower() == code.strip().lower():
            return c
    return None
 
 
def find_server(name):
    for s in servers:
        if s.name.lower() == name.strip().lower() or s.ip == name.strip():
            return s
    return None
 
 
def is_authorized(student, server, service):
    """Autorizado si esta matriculado en algun curso ACTIVE que tenga al servidor
    y al servicio dentro de sus allowed_services."""
    return any(student.name in c.students and c.allows(server.name, service.name) for c in courses)
 
 

def base_url():
    return f"http://{CONTROLLER_IP}:{REST_PORT}"
 
 
def _devices(params):
    r = requests.get(base_url() + "/wm/device/", params=params, timeout=5)
    data = r.json()
    if isinstance(data, dict):           # algunas versiones devuelven {"devices": [...]}
        data = data.get("devices", [])
    return data
 
 
def _port(p):
    if isinstance(p, dict):
        return str(p.get("portNumber", p.get("shortPortNumber")))
    return str(p)
 
 
def get_attachment_point(controller_ip, mac):
    devs = _devices({"mac": mac})
    if not devs:
        return None, None
    attachment_points = devs[0].get("attachmentPoint", [])
    if not attachment_points:
        return None, None
    ap = attachment_points[0]
    return ap["switchDPID"], _port(ap["port"])
 
 
def get_mac_by_ip(ip):
    devs = _devices({"ipv4": ip})
    for d in devs:
        macs = d.get("mac", [])
        if macs:
            return macs[0].lower()
    return None
 
 
def get_route(controller_ip, src_mac, dst_mac):
    src_sw_dpid, src_sw_port = get_attachment_point(controller_ip, src_mac)
    dst_sw_dpid, dst_sw_port = get_attachment_point(controller_ip, dst_mac)
    if not src_sw_dpid or not dst_sw_dpid:
        raise RuntimeError("Floodlight no conoce el punto de conexion de alguno de los hosts "
                           "(haz ping desde H1 y H3 para que el controlador los descubra).")
    endpoint = f'/wm/topology/route/{src_sw_dpid}/{src_sw_port}/{dst_sw_dpid}/{dst_sw_port}/json'
    response = requests.get(base_url() + endpoint, timeout=5)
    return response.json()
 
 
def parse_route(route):
    """La ruta viene como lista de (switch, puerto): [sw1 in, sw1 out, sw2 in, sw2 out, ...]
    Se convierte a [(dpid, in_port, out_port), ...]."""
    hops = []
    for i in range(0, len(route) - 1, 2):
        a, b = route[i], route[i + 1]
        hops.append((a["switch"], _port(a["port"]), _port(b["port"])))
    return hops
 
 
def detect_sfp():
    global SFP_BASE
    if SFP_BASE:
        return SFP_BASE
    for cand in SFP_CANDIDATES:
        try:
            r = requests.get(base_url() + cand + "/list/all/json", timeout=3)
            if r.status_code == 200:
                SFP_BASE = cand
                return SFP_BASE
        except requests.RequestException:
            pass
    SFP_BASE = SFP_CANDIDATES[0]
    return SFP_BASE
 
 
def _post_flow(flow):
    r = requests.post(base_url() + detect_sfp() + "/json", data=json.dumps(flow), timeout=5)
    return flow["name"], r.status_code, r.text.strip()
 
 
def _delete_flow(name):
    r = requests.delete(base_url() + detect_sfp() + "/json", data=json.dumps({"name": name}), timeout=5)
    return name, r.status_code, r.text.strip()
 
 
async def _run_parallel(func, items):
    loop = asyncio.get_event_loop()
    tasks = [loop.run_in_executor(None, func, it) for it in items]
    return await asyncio.gather(*tasks)
 
 
def push_flows(flows):
    return asyncio.run(_run_parallel(_post_flow, flows))
 
 
def delete_flows(names):
    return asyncio.run(_run_parallel(_delete_flow, names))

def build_route(handler, hops, student, server, server_mac, services):
    """Genera los flow entries (L2+L3+L4, ida y vuelta + ARP) para cada switch de la ruta
    y los inserta asincronamente en el controlador."""
    flows = []
    for idx, (dpid, in_port, out_port) in enumerate(hops):
        for svc in services:
            proto = "0x06" if svc.protocol == "TCP" else "0x11"
            l4 = "tcp" if svc.protocol == "TCP" else "udp"
            # alumno -> servidor
            flows.append({
                "switch": dpid, "name": f"{handler}_{idx}_{svc.name}_fwd",
                "priority": PRIORITY, "active": "true", "in_port": in_port,
                "eth_type": "0x0800", "eth_src": student.mac, "eth_dst": server_mac,
                "ipv4_dst": server.ip, "ip_proto": proto, f"{l4}_dst": str(svc.port),
                "actions": f"output={out_port}"})
            # servidor -> alumno
            flows.append({
                "switch": dpid, "name": f"{handler}_{idx}_{svc.name}_rev",
                "priority": PRIORITY, "active": "true", "in_port": out_port,
                "eth_type": "0x0800", "eth_src": server_mac, "eth_dst": student.mac,
                "ipv4_src": server.ip, "ip_proto": proto, f"{l4}_src": str(svc.port),
                "actions": f"output={in_port}"})
        # ARP en ambos sentidos
        flows.append({
            "switch": dpid, "name": f"{handler}_{idx}_arp_fwd", "priority": PRIORITY,
            "active": "true", "in_port": in_port, "eth_type": "0x0806",
            "eth_src": student.mac, "actions": f"output={out_port}"})
        flows.append({
            "switch": dpid, "name": f"{handler}_{idx}_arp_rev", "priority": PRIORITY,
            "active": "true", "in_port": out_port, "eth_type": "0x0806",
            "eth_src": server_mac, "actions": f"output={in_port}"})
 
    results = push_flows(flows)
    ok = True
    for name, code, text in results:
        print(f"   {name:<28} -> HTTP {code} {text}")
        if code != 200:
            ok = False
    return [(f["switch"], f["name"]) for f in flows], ok
 

def ask(msg):
    return input(f"{msg}: ").strip()
 
 
def choose(title, options):
    print(f"\n--- {title} ---")
    for i, o in enumerate(options, 1):
        print(f"{i}) {o}")
    print("0) Regresar")
    return ask(">>>")
 
 
def menu_importar():
    global students, servers, courses
    fn = ask("Nombre de archivo (ej. database.yaml)")
    try:
        students, servers, courses = load_data(fn)
        print(f"Importado: {len(students)} alumnos, {len(courses)} cursos, {len(servers)} servidores.")
    except Exception as e:
        print(f"Error al importar: {e}")
 
 
def menu_exportar():
    fn = ask("Nombre de archivo (ej. export.yaml)")
    try:
        save_data(fn)
        print(f"Datos exportados en {fn}")
    except Exception as e:
        print(f"Error al exportar: {e}")
 
 
def menu_cursos():
    while True:
        op = choose("CURSOS", ["Listar", "Mostrar detalles", "Gestionar alumnos"])
        if op == "1":
            print(f"\n{'CODIGO':<8} {'NOMBRE':<32} ESTADO")
            for c in courses:
                print(c)
        elif op == "2":
            c = find_course(ask("Codigo del curso"))
            if not c:
                print("Curso no encontrado."); continue
            print(f"\nCodigo : {c.code}\nNombre : {c.name}\nEstado : {c.state}")
            print("Alumnos:")
            for n in c.students:
                s = find_student(n)
                print(f"   - {s if s else n}")
            print("Servidores:")
            for s in c.servers:
                print(f"   - {s['name']}: {', '.join(s['allowed_services'])}")
        elif op == "3":
            c = find_course(ask("Codigo del curso"))
            if not c:
                print("Curso no encontrado."); continue
            sub = choose(f"Gestionar alumnos de {c.code}", ["Agregar alumno", "Eliminar alumno"])
            s = find_student(ask("Nombre o codigo del alumno")) if sub in ("1", "2") else None
            if sub in ("1", "2") and not s:
                print("Alumno no encontrado."); continue
            if sub == "1":
                print("Alumno agregado." if c.add_student(s.name) else "El alumno ya estaba matriculado.")
            elif sub == "2":
                print("Alumno eliminado." if c.remove_student(s.name) else "El alumno no estaba en el curso.")
        elif op == "0":
            return
 
 
def menu_alumnos():
    while True:
        op = choose("ALUMNOS", ["Crear", "Listar", "Mostrar detalles"])
        if op == "1":
            name = ask("Nombre")
            mac = ask("MAC")
            if find_student(name):
                print("Ya existe un alumno con ese nombre."); continue
            s = Student(name, mac)
            students.append(s)
            print(f"Alumno creado: {s}")
        elif op == "2":
            f = ask("Filtrar por codigo de curso (Enter = todos)")
            if f:
                c = find_course(f)
                if not c:
                    print("Curso no encontrado."); continue
                lst = [s for s in students if s.name in c.students]
                print(f"\nAlumnos del curso {c.code} - {c.name}:")
            else:
                lst = students
                print("\nTodos los alumnos:")
            for s in lst:
                print(f"   {s}")
        elif op == "3":
            s = find_student(ask("Nombre o codigo del alumno"))
            if not s:
                print("Alumno no encontrado."); continue
            print(f"\nCodigo : {s.code}\nNombre : {s.name}\nMAC    : {s.mac}")
            print("Cursos : " + ", ".join(c.code for c in courses if s.name in c.students))
        elif op == "0":
            return
 
 
def menu_servidores():
    while True:
        op = choose("SERVIDORES", ["Listar", "Mostrar servicios"])
        if op == "1":
            for s in servers:
                print(f"   {s}")
        elif op == "2":
            s = find_server(ask("Nombre del servidor (ej. Server 3)"))
            if not s:
                print("Servidor no encontrado."); continue
            print(f"\n{s}\nServicios:")
            for sv in s.services:
                print(f"   - {sv}")
        elif op == "0":
            return
 
 
def menu_politicas():
    while True:
        op = choose("POLITICAS", ["Cursos con acceso a un servicio de un servidor"])
        if op == "1":
            srv = find_server(ask("Servidor (ej. Server 3)"))
            if not srv:
                print("Servidor no encontrado."); continue
            svc = ask("Servicio (ej. ssh)")
            print(f"\nCursos con acceso a '{svc}' en {srv.name}:")
            for c in courses:
                for s in c.servers:
                    if s["name"] == srv.name and svc in s["allowed_services"]:
                        print(f"   {c}")
        elif op == "0":
            return
 
 
def menu_conexiones():
    while True:
        op = choose("CONEXIONES", ["Crear", "Listar", "Mostrar detalles", "Borrar"])
        if op == "1":
            crear_conexion()
        elif op == "2":
            if not connections:
                print("No hay conexiones.")
            for c in connections:
                print(f"   {c}")
        elif op == "3":
            c = next((x for x in connections if x.handler == ask("Handler")), None)
            if not c:
                print("Handler no encontrado."); continue
            print(c)
            for dpid, i, o in c.path:
                print(f"   switch {dpid}: in_port {i} -> out_port {o}")
        elif op == "4":
            borrar_conexion()
        elif op == "0":
            return
 
 
def crear_conexion():
    st = find_student(ask("Alumno (nombre o codigo)"))
    if not st:
        print("Alumno no encontrado."); return
    srv = find_server(ask("Servidor (ej. Server 3)"))
    if not srv:
        print("Servidor no encontrado."); return
    svc_in = ask("Servicio (ej. ssh) o Enter para todos los permitidos")
    cand = [srv.get_service(svc_in)] if svc_in else srv.services
    if None in cand:
        print("Servicio no existe en el servidor."); return
 
    allowed = [s for s in cand if is_authorized(st, srv, s)]
    if not allowed:
        print(f"ERROR: {st.name} NO esta autorizado a acceder a "
              f"{svc_in or 'ningun servicio'} de {srv.name}.")
        return
 
    try:
        server_mac = get_mac_by_ip(srv.ip)
        if not server_mac:
            print(f"Floodlight no conoce la MAC de {srv.ip}. Haz ping desde el servidor para que se descubra.")
            return
        hops = parse_route(get_route(CONTROLLER_IP, st.mac, server_mac))
        if not hops:
            print("No se obtuvo ruta del controlador."); return
    except Exception as e:
        print(f"Error consultando al controlador: {e}"); return
 
    handler = uuid.uuid4().hex[:8]
    print(f"Instalando flows (handler {handler}) en {len(hops)} switch(es)...")
    flows, ok = build_route(handler, hops, st, srv, server_mac, allowed)
    conn = Connection(st, srv, allowed, hops, flows)
    conn.handler = handler
    connections.append(conn)
    print(("Conexion creada: " if ok else "Conexion creada con errores: ") + str(conn))
 
 
def borrar_conexion():
    h = ask("Handler de la conexion")
    c = next((x for x in connections if x.handler == h), None)
    if not c:
        print("Handler no encontrado."); return
    for name, code, text in delete_flows([n for _, n in c.flows]):
        print(f"   {name:<28} -> HTTP {code} {text}")
    connections.remove(c)
    print(f"Conexion {h} eliminada.")
 
 
# Menu principal
def menu():
    print("\n" + "#" * 50)
    print("Network Policy manager de la UPSM")
    print("#" * 50)
    print("\nSeleccione una opción:\n")
    for i, o in enumerate(["Importar", "Exportar", "Cursos", "Alumnos",
                           "Servidores", "Políticas", "Conexiones", "Salir"], 1):
        print(f"{i}) {o}")
    return input(">>> ").strip()
 
 
def main():
    global students, servers, courses
    students, servers, courses = load_data("database.yaml")
    actions = {"1": menu_importar, "2": menu_exportar, "3": menu_cursos, "4": menu_alumnos,
               "5": menu_servidores, "6": menu_politicas, "7": menu_conexiones}
    while True:
        op = menu()
        if op == "8":
            print("Saliendo...")
            break
        if op in actions:
            actions[op]()
        else:
            print("Opción inválida.")
 
 
if __name__ == "__main__":
    main()
 