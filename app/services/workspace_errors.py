class WorkspaceError(Exception):
    def __init__(self, detail: str | dict):
        self.detail = detail
        super().__init__(str(detail))


class WorkspaceNotFound(WorkspaceError):
    pass


class WorkspaceForbidden(WorkspaceError):
    pass


class WorkspaceConflict(WorkspaceError):
    pass


class WorkspaceInvalid(WorkspaceError):
    pass
