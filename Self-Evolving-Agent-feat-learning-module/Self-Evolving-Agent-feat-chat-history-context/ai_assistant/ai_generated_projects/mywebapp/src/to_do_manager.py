def add_item(to_do_list, task):
    """Adds a new to-do item to the list."""
    to_do_list.append({"task": task, "complete": False})
    print(f"Added item: {task}")

def delete_item(to_do_list, task):
    """Removes a to-do item from the list."""
    for i, item in enumerate(to_do_list):
        if item["task"] == task:
            del to_do_list[i]
            print(f"Deleted item: {task}")
            return
    print(f"Item not found: {task}")

def mark_complete(to_do_list, task):
    """Marks an existing to-do item as complete."""
    for item in to_do_list:
        if item["task"] == task:
            item["complete"] = True
            print(f"Marked '{task}' as complete.")
            return
    print(f"Item not found: {task}")

def list_items(to_do_list):
    """Displays all the to-do items in the list."""
    if not to_do_list:
        print("No items in the to-do list.")
        return

    for i, item in enumerate(to_do_list):
        status = "[X]" if item["complete"] else "[ ]"
        print(f"{i+1}. {status} {item['task']}")

if __name__ == '__main__':
    to_do_list = []

    while True:
        print("\nTo-Do List Manager")
        print("1. Add item")
        print("2. Delete item")
        print("3. Mark complete")
        print("4. List items")
        print("5. Exit")

        choice = input("Enter your choice: ")

        if choice == '1':
            task = input("Enter task description: ")
            add_item(to_do_list, task)
        elif choice == '2':
            task = input("Enter task to delete: ")
            delete_item(to_do_list, task)
        elif choice == '3':
            task = input("Enter task to mark complete: ")
            mark_complete(to_do_list, task)
        elif choice == '4':
            list_items(to_do_list)
        elif choice == '5':
            break
        else:
            print("Invalid choice. Please try again.")