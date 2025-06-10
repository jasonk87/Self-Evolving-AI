def display_menu():
    print("\nTo-Do List Manager")
    print("1. Add Task")
    print("2. View Tasks")
    print("3. Delete Task")
    print("4. Quit")

def get_user_input():
    try:
        return int(input("Enter your choice: "))
    except ValueError:
        print("Invalid input. Please enter a number.")
        return None

def process_command(choice):
    if choice == 1:
        print("Adding task. (Not implemented in this example)")
    elif choice == 2:
        print("Viewing tasks. (Not implemented in this example)")
    elif choice == 3:
        print("Deleting task. (Not implemented in this example)")
    elif choice == 4:
        print("Exiting...")
        return False  # Signal to exit the main loop
    else:
        print("Invalid choice.")
    return True #Signal to continue

def main():
    running = True
    while running:
        display_menu()
        choice = get_user_input()
        if choice is None:
            continue
        running = process_command(choice)

if __name__ == "__main__":
    main()