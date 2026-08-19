"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

type Department = { id: string; name: string };
type Role = { id: string; name: string; is_system?: boolean };
type Employee = {
  id: string;
  name: string;
  email: string;
  role: string;
  department_id: string;
  custom_role_id?: string;
};

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  employee: Employee | null; // null = create mode
  departments: Department[];
  roles?: Role[];
  onSaved: () => void;
};

export function EmployeeDialog({
  open,
  onOpenChange,
  employee,
  departments,
  roles = [],
  onSaved,
}: Props) {
  const isEdit = !!employee;
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("employee");
  const [deptId, setDeptId] = useState("");
  const [customRoleId, setCustomRoleId] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [createdRoles, setCreatedRoles] = useState<Role[]>([]);
  const [createdDepartments, setCreatedDepartments] = useState<Department[]>([]);
  const [inlinePrompt, setInlinePrompt] = useState({
    open: false,
    title: "",
    label: "",
    value: "",
    saving: false,
    error: "",
    onSubmit: async (val: string) => {},
  });

  // Derived, not mirrored into state: the lists arrive asynchronously, and anything created
  // inline here lives only client-side until the parent refetches — copying the props over the
  // local list would drop it and leave the Select rendering a raw UUID.
  const localRoles = useMemo(() => {
    const fromProps = roles.filter(r => !r.is_system);
    const known = new Set(fromProps.map(r => r.id));
    return [...fromProps, ...createdRoles.filter(r => !known.has(r.id))];
  }, [roles, createdRoles]);

  const localDepartments = useMemo(() => {
    const known = new Set(departments.map(d => d.id));
    return [...departments, ...createdDepartments.filter(d => !known.has(d.id))];
  }, [departments, createdDepartments]);

  const handleCreateDepartment = () => {
    setInlinePrompt({
      open: true,
      title: "Create Department",
      label: "Department Name",
      value: "",
      saving: false,
      error: "",
      onSubmit: async (val) => {
        const newDept = await api<Department>("/api/departments", {
          method: "POST",
          body: { name: val, description: "" }
        });
        setCreatedDepartments(prev => [...prev, newDept]);
        setDeptId(newDept.id);
        setInlinePrompt(p => ({ ...p, open: false }));
      }
    });
  };

  const handleCreateRole = () => {
    setInlinePrompt({
      open: true,
      title: "Create Position",
      label: "Position Name",
      value: "",
      saving: false,
      error: "",
      onSubmit: async (val) => {
        const newRole = await api<Role>("/api/roles", {
          method: "POST",
          body: { name: val, permissions: [] }
        });
        setCreatedRoles(prev => [...prev, newRole]);
        setCustomRoleId(newRole.id);
        setInlinePrompt(p => ({ ...p, open: false }));
      }
    });
  };

  const submitInlinePrompt = async () => {
    if (!inlinePrompt.value.trim()) return;
    setInlinePrompt(p => ({ ...p, saving: true, error: "" }));
    try {
      await inlinePrompt.onSubmit(inlinePrompt.value.trim());
    } catch (err) {
      setInlinePrompt(p => ({ ...p, saving: false, error: err instanceof Error ? err.message : String(err) }));
    }
  };

  // Keyed on the employee identity and `open` only. Depending on the `departments` prop meant
  // that the async fetch landing while the dialog was already open re-ran this and wiped the
  // name, email and password the user had typed. The default department is derived below
  // instead, so a late-arriving list can fill an untouched field without resetting anything.
  useEffect(() => {
    if (employee) {
      setName(employee.name);
      setEmail(employee.email);
      setRole(employee.role);
      setDeptId(employee.department_id);
      setCustomRoleId(employee.custom_role_id || "");
      setPassword("");
    } else {
      setName("");
      setEmail("");
      setPassword("");
      setRole("employee");
      setDeptId("");
      setCustomRoleId("");
    }
    setError("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [employee?.id, open]);

  const effectiveDeptId = deptId || localDepartments[0]?.id || "";

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError("");

    try {
      const body: Record<string, string | null> = {
        name,
        email,
        department_id: effectiveDeptId,
        custom_role_id: customRoleId || null,
      };
      if (isAdmin) {
        body.role = role;
        if (password) body.password = password;
      } else if (!isEdit) {
        body.role = "employee";
      }

      if (isEdit) {
        await api(`/api/employees/${employee.id}`, { method: "PUT", body });
      } else {
        if (!password) {
          setError("Password is required");
          setSaving(false);
          return;
        }
        await api("/api/employees", { method: "POST", body });
      }

      onSaved();
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="text-xl">
            {isEdit ? "Edit Employee" : "Add Employee"}
          </DialogTitle>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4 mt-2">
          <div className="flex flex-col gap-2">
            <Label htmlFor="emp-name">Name</Label>
            <Input
              id="emp-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              className="bg-background"
            />
          </div>

          <div className="flex flex-col gap-2">
            <Label htmlFor="emp-email">Email</Label>
            <Input
              id="emp-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="bg-background"
            />
          </div>

          {(!isEdit || isAdmin) && (
          <div className="flex flex-col gap-2">
            <Label htmlFor="emp-password">
              Password {isEdit && "(leave blank to keep current)"}
            </Label>
            <Input
              id="emp-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={isEdit ? "••••••••" : "Min 8 characters"}
              className="bg-background"
            />
            {isEdit && isAdmin && (
              <p className="text-xs text-muted-foreground">
                Only a system admin can reset passwords.
              </p>
            )}
          </div>
          )}

          <div className="grid grid-cols-2 gap-4">
            <div className="flex flex-col gap-2">
              <Label>System Role</Label>
              <Select value={role} onValueChange={(v) => v && setRole(v)} disabled={!isAdmin && isEdit}>
                <SelectTrigger className="bg-background">
                  {role === "admin" ? "Admin" : role === "employee" ? "Employee" : <SelectValue />}
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="employee">Employee</SelectItem>
                  {isAdmin && <SelectItem value="admin">Admin</SelectItem>}
                </SelectContent>
              </Select>
            </div>

            <div className="flex flex-col gap-2">
              <Label>Department</Label>
              <Select
                value={effectiveDeptId}
                onValueChange={(v) => {
                  if (v === "__new__") {
                    handleCreateDepartment();
                    return;
                  }
                  if (v) setDeptId(v);
                }}
              >
                <SelectTrigger className="bg-background">
                  {effectiveDeptId ? (localDepartments.find(d => d.id === effectiveDeptId)?.name || effectiveDeptId) : <SelectValue placeholder="Select" />}
                </SelectTrigger>
                <SelectContent className="!w-max min-w-(--anchor-width)">
                  {localDepartments.map((d) => (
                    <SelectItem key={d.id} value={d.id}>
                      {d.name}
                    </SelectItem>
                  ))}
                  <div className="h-px bg-border my-1 -mx-1" />
                  <SelectItem value="__new__" className="text-primary font-medium focus:text-primary">
                    <span className="flex items-center gap-2">
                      <span className="material-symbols-outlined text-sm">add</span>
                      Create new department...
                    </span>
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          {role === "employee" && (
            <div className="flex flex-col gap-2">
              <Label>Position</Label>
              <Select
                value={customRoleId || "__none__"}
                onValueChange={(v) => {
                  if (v === "__new__") {
                    handleCreateRole();
                    return;
                  }
                  setCustomRoleId(v === "__none__" ? "" : (v ?? ""));
                }}
              >
                <SelectTrigger className="bg-background">
                  {customRoleId ? (localRoles.find(r => r.id === customRoleId)?.name || customRoleId) : "None"}
                </SelectTrigger>
                <SelectContent className="!w-max min-w-(--anchor-width)">
                  <SelectItem value="__none__">None</SelectItem>
                  {localRoles.map((r) => (
                    <SelectItem key={r.id} value={r.id}>
                      {r.name}
                    </SelectItem>
                  ))}
                  <div className="h-px bg-border my-1 -mx-1" />
                  <SelectItem value="__new__" className="text-primary font-medium focus:text-primary">
                    <span className="flex items-center gap-2">
                      <span className="material-symbols-outlined text-sm">add</span>
                      Create new position...
                    </span>
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
          )}

          {error && (
            <p className="text-destructive text-sm bg-destructive/10 px-3 py-2 rounded-lg">
              {error}
            </p>
          )}

          <div className="flex justify-end gap-2 mt-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={saving}
              className="bg-primary text-primary-foreground hover:bg-primary/90"
            >
              {saving ? "Saving..." : isEdit ? "Update" : "Create"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>

      <Dialog open={inlinePrompt.open} onOpenChange={(o) => setInlinePrompt(p => ({ ...p, open: o }))}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{inlinePrompt.title}</DialogTitle>
          </DialogHeader>
          <div className="flex flex-col gap-4 py-2">
            <div className="flex flex-col gap-2">
              <Label>{inlinePrompt.label}</Label>
              <Input 
                value={inlinePrompt.value}
                onChange={e => setInlinePrompt(p => ({ ...p, value: e.target.value }))}
                autoFocus
                onKeyDown={e => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    submitInlinePrompt();
                  }
                }}
              />
            </div>
            {inlinePrompt.error && (
              <p className="text-destructive text-sm">{inlinePrompt.error}</p>
            )}
          </div>
          <div className="flex justify-end gap-2 mt-2">
            <Button type="button" variant="outline" onClick={() => setInlinePrompt(p => ({ ...p, open: false }))}>
              Cancel
            </Button>
            <Button 
              disabled={inlinePrompt.saving || !inlinePrompt.value.trim()} 
              onClick={submitInlinePrompt}
            >
              {inlinePrompt.saving ? "Saving..." : "Create"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
