const SkeletonCard = () => (
  <article className="bg-white dark:bg-[rgb(22,22,22)] border border-gray-200 dark:border-[rgb(47,51,54)] rounded-2xl p-3 lg:p-4 animate-pulse">
    {/* Avatar + title */}
    <div className="flex items-center gap-2 mb-3">
      <div className="w-6 h-6 rounded-full bg-gray-200 dark:bg-gray-700" />
      <div className="h-3 bg-gray-200 dark:bg-gray-700 rounded w-24" />
      <div className="h-3 bg-gray-200 dark:bg-gray-700 rounded w-16" />
    </div>
    {/* Description */}
    <div className="space-y-2 mb-3">
      <div className="h-3 bg-gray-200 dark:bg-gray-700 rounded w-full" />
      <div className="h-3 bg-gray-200 dark:bg-gray-700 rounded w-3/4" />
    </div>
    {/* Image placeholder */}
    <div className="h-32 bg-gray-200 dark:bg-gray-700 rounded-xl" />
    {/* Action bar */}
    <div className="flex gap-4 mt-3">
      <div className="h-3 bg-gray-200 dark:bg-gray-700 rounded w-16" />
      <div className="h-3 bg-gray-200 dark:bg-gray-700 rounded w-20" />
      <div className="h-3 bg-gray-200 dark:bg-gray-700 rounded w-14" />
    </div>
  </article>
);

export default SkeletonCard;
