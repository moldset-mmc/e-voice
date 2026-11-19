"""Unix freezer entry. Child processes must dispatch before importing Tk."""
if __name__ == '__main__':
    from multiprocessing import freeze_support
    freeze_support()
    from desktop_entry import dispatch
    dispatch()
