import to_do_manager

def main():
    while True:
        print("\nTo-Do List Application")
        print("1. Add task")
        print("2. List tasks")
        print("3. Mark task as complete")
        print("4. Exit")

        choice = input("Enter your choice: ")

        if choice == '1':
            task = input("Enter task description: ")
            to_do_manager.add_task(task)
        elif choice == '2':
            tasks = to_do_manager.get_all_tasks()
            if tasks:
                for i, task in enumerate(tasks):
                    print(f"{i+1}. {task}")
            else:
                print("No tasks in the to-do list.")
        elif choice == '3':
            try:
                task_number = int(input("Enter the number of the task to mark as complete: ")) - 1
                if 0 <= task_number < len(to_do_manager.get_all_tasks()):
                    to_do_manager.mark_task_complete(task_number)
                else:
                    print("Invalid task number.")
            except ValueError:
                print("Invalid input. Please enter a number.")
        elif choice == '4':
            print("Exiting application.")
            break
        else:
            print("Invalid choice. Please try again.")

if __name__ == "__main__":
    main()